"""
星星骰娘-重骰版！AstrBot TRPG 骰子插件主模块。

架构：DicePlugin 通过多重继承组合各 Mixin 的功能模块。
命令路由集中在 handler/router.py 的 RouterMixin.identify_command() 中处理。
"""

import asyncio
import time

from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig

from .component.astrbot_compat import filter, AstrMessageEvent

from .component import dice as dice_mod
from .component import illustration as illustration_mod
from .component.beautify import (
    assemble_decoration,
    ensure_protocol,
    select_system_prompt,
)
from .component.output import get_output, get_config, set_config
from .component.utils import roll_character, format_character, roll_dnd_character, format_dnd_character
from .component.rules import modify_coc_great_sf_rule_command
from .component.log import JSONLoggerCore

from .handler import (
    DiceMixin,
    CharacterMixin,
    BotMixin,
    DismissMixin,
    CoCMixin,
    InitiativeMixin,
    LogMixin,
    LLMToolsMixin,
    RouterMixin,
)

PLUGIN_NAME = "astrbot_plugin_trpgdice_rerolled"

logger_core = JSONLoggerCore()

async def init():
    await logger_core.initialize()

@register(PLUGIN_NAME, "星空凌", "TRPG玩家用骰", "2.1.1")
class DicePlugin(
    DiceMixin,
    CharacterMixin,
    BotMixin,
    DismissMixin,
    CoCMixin,
    InitiativeMixin,
    LogMixin,
    LLMToolsMixin,
    RouterMixin,
    Star,
):
    def __init__(self, context: Context, config: AstrBotConfig):
        self.wakeup_prefix = [".", "。", "/"]
        self.logger_core = logger_core
        self.start_time = time.time()  # 供 .bot 显示本次运行时长
        self._illustration_times: dict[str, float] = {}  # 按群的配图冷却时间
        self._illustration_busy: set[str] = set()  # 正在绘制的群，避免并发重复生图
        # 初始化配置系统
        set_config(config)

        super().__init__(context)

        # 后台插件页「联动检查」调用的只读接口
        try:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/check_draw",
                self.check_draw_handler,
                ["GET"],
                "检查万象画卷插件与高光配图状态",
            )
        except Exception:
            pass

    async def save_log(self, group_id, content):
        # TODO: 昵称应从配置读取，当前硬编码为 "风铃Velinithra"
        await self.logger_core.add_message(
            group_id=group_id,
            user_id="Bot",
            nickname="风铃Velinithra",
            timestamp=int(time.time()),
            text=content,
            isDice=True
        )

    def _get_beautify_provider(self, event: AstrMessageEvent):
        """美化优先使用 llm_mode.provider_id 指定的 Provider，否则回退当前对话模型。"""
        provider_id = str(get_config("llm_mode.provider_id", "") or "").strip()
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if provider:
                    return provider
            except Exception:
                pass
        try:
            return self.context.get_using_provider(umo=event.unified_msg_origin)
        except Exception:
            return None

    async def _beautify(self, raw_text: str, event: AstrMessageEvent) -> str:
        """LLM 美化入口。

        默认 decorate 模式：core 固定，模型只返回 prefix/suffix，最终拼接为
        prefix + core + suffix，模型无法改写 core 里的数值与判定。
        rewrite 模式保留旧行为：把整段文本交给模型重写。
        """
        if not get_config("llm_mode.enabled", False):
            return raw_text
        try:
            prov = self._get_beautify_provider(event)
            if not prov:
                return raw_text

            mode = str(get_config("llm_mode.output_mode", "decorate") or "decorate").strip()
            if mode not in ("decorate", "rewrite"):
                mode = "decorate"
            configured_prompt = str(
                get_config("llm_mode.system_prompt", "") or ""
            ).strip()
            base_prompt = select_system_prompt(mode, configured_prompt)

            if mode == "decorate":
                system_prompt = ensure_protocol(base_prompt)
                prompt = f"core：\n{raw_text}"
            else:
                system_prompt = base_prompt
                prompt = raw_text

            model = get_config("llm_mode.model", "") or None
            # 美化只是锦上添花：给一个硬超时，provider 卡住/重试时也不拖住掷骰结果
            try:
                timeout = max(1, int(get_config("llm_mode.timeout", 15)))
            except (TypeError, ValueError):
                timeout = 15
            resp = await asyncio.wait_for(
                prov.text_chat(
                    prompt=prompt,
                    contexts=[],
                    system_prompt=system_prompt,
                    model=model,
                ),
                timeout=timeout,
            )
            completion = resp.completion_text or ""
            if mode == "decorate":
                return assemble_decoration(raw_text, completion)
            return completion or raw_text
        except Exception:
            return raw_text

    async def check_draw_handler(self):
        """后台插件页「联动检查」：返回万象画卷可用状态与本插件配图配置。"""
        try:
            from quart import jsonify
        except Exception:
            return {
                "success": False,
                "message": "当前环境不支持 Quart Web API。",
            }

        plugin_name = str(
            get_config("log_illustration.plugin_name", illustration_mod.DEFAULT_DRAW_PLUGIN)
            or ""
        )
        try:
            _draw_plugin, draw_status = illustration_mod.find_draw_plugin(
                self.context, plugin_name
            )
        except Exception as exc:
            return jsonify({
                "success": False,
                "message": f"检查失败：{str(exc)[:120]}",
            })

        try:
            max_per_call = max(1, int(get_config("log_illustration.max_per_call", 3) or 3))
        except (TypeError, ValueError):
            max_per_call = 3

        return jsonify({
            "success": True,
            "checked_at": int(time.time()),
            "draw": draw_status,
            "dice": {
                "enabled": bool(get_config("log_illustration.enabled", True)),
                "style_prompt": str(get_config("log_illustration.style_prompt", "") or ""),
                "aspect_ratio": str(get_config("log_illustration.aspect_ratio", "") or ""),
                "respect_permission": bool(
                    get_config("log_illustration.respect_permission", False)
                ),
                "record_usage": bool(get_config("log_illustration.record_usage", False)),
                "max_per_call": max_per_call,
            },
            "message": draw_status.get("message", ""),
        })

    # ======================== 杂项命令 ============================= #

    @filter.command("coc")
    async def generate_coc_character(self, event: AstrMessageEvent, x: int = 1):
        characters = [roll_character() for _ in range(x)]
        results = []
        for i, char in enumerate(characters):
            results.append(format_character(char, index=i+1))
        text = get_output("character_list.coc", characters="\n\n".join(results))
        text = await self._beautify(text, event)
        yield event.plain_result(text)

    @filter.command("dnd")
    async def generate_dnd_character(self, event: AstrMessageEvent, x: int = 1):
        characters = [roll_dnd_character() for _ in range(x)]
        results = []
        for i, char in enumerate(characters):
            results.append(format_dnd_character(char, index=i+1))
        text = get_output("character_list.dnd", characters="\n\n".join(results))
        text = await self._beautify(text, event)
        yield event.plain_result(text)

    @filter.command("dicehelp")
    async def help(self, event: AstrMessageEvent):
        help_text = (
            "基础掷骰\n"
            "`/r 1d100` - 掷 1 个 100 面骰\n"
            "`/r 3d6+2d4-1d8` - 掷 3 个 6 面骰 + 2 个 4 面骰 - 1 个 8 面骰\n"
            "`/r 3#1d20` - 掷 1d20 骰 3 次\n"
            "`/r 10d6k5` - 掷 10 个 6 面骰，保留最高 5 个\n\n"

            "人物卡管理\n"
            "`/pc create 名称 属性值` - 创建人物卡\n"
            "`/pc show` - 显示当前人物卡\n"
            "`/pc list` - 列出所有人物卡\n"
            "`/pc change 名称` - 切换当前人物卡\n"
            "`/pc update 属性 值/公式` - 更新人物卡属性\n"
            "`/pc delete 名称` - 删除人物卡\n"
            "`/st 属性+值` - 修改属性（没有人物卡时自动以当前昵称建卡）\n\n"

            "CoC 相关\n"
            "`/coc x` - 生成 x 个 CoC 角色数据\n"
            "`/ra 技能名` - 进行技能骰\n"
            "`/rap n 技能名` - 带 n 个惩罚骰的技能骰\n"
            "`/rab n 技能名` - 带 n 个奖励骰的技能骰\n"
            "`/sc 1d6/1d10` - 进行 San Check\n"
            "`/ti` - 生成临时疯狂症状\n"
            "`/li` - 生成长期疯狂症状\n"
            "`/en 技能名 [技能值]` - 技能成长\n"
            "`/setcoc 规则编号` - 设置COC规则\n\n"

            "DnD 相关\n"
            "`/dnd x` - 生成 x 个 DnD 角色属性\n"
            "`/init` - 显示当前先攻列表\n"
            "`/init clr` - 清空先攻列表\n"
            "`/init del [角色名]` - 删除角色先攻（默认为用户名）\n"
            "`/ri +/- x` - 以x的调整值投掷先攻\n"
            "`/ri x [角色名]` - 将角色（默认为用户名）的先攻设置为x\n"
            "`/ed` - 结束当前回合\n"
            "`/fireball n` - 施放 n 环火球术，计算伤害\n\n"

            "插件介绍与开关（按群独立，群内所有人可用）\n"
            "`/bot` - 查看骰娘简介（可在插件配置中自定义）\n"
            "`/bot off` - 关闭本群骰娘功能（不影响其它群与其他插件）\n"
            "`/bot on` - 重新开启本群\n"
            "`/dismiss` - 立即让骰娘退出本群（任何人可用）\n\n"

            "Log 管理\n"
            "`/log new <日志名>` - 开始新的日志会话\n"
            "`/log off` - 暂停当前的日志会话（并生成本段小结 + 高光）\n"
            "`/log on` - 开始当前的日志会话\n"
            "`/log end` - 结束当前的日志会话\n"
            "`/log del <日志名>` - 删除日志会话\n"
            "`/log get <日志名>` - 获取日志会话\n"
            "`/log stat <日志名>` - 获取日志会话统计信息\n"
            "`/log draw [序号|cover|all]` - 用万象画卷把高光画成插图\n"
        )

        yield event.plain_result(help_text)

    @filter.command("fireball")
    async def fireball_cmd(self, event: AstrMessageEvent, ring: int = 3):
        ring = max(3, min(ring, 20))
        result = dice_mod.fireball(ring)
        result = await self._beautify(result, event)
        yield event.plain_result(result)

    @filter.command("jrrp")
    async def roll_RP_cmd(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        name = await self.get_roll_display_name(event)
        result = dice_mod.roll_RP(user_id, name)
        result = await self._beautify(result, event)
        yield event.plain_result(result)

    @filter.command("setcoc")
    async def setcoc_cmd(self, event: AstrMessageEvent, command: str = " "):
        group_id = event.get_group_id()
        result = modify_coc_great_sf_rule_command(group_id, command)
        yield event.plain_result(result)
