"""
`.bot` 指令 Mixin（按群独立开关）。

- ``.bot``       立即输出一段骰娘简介（可在插件配置中自定义，见 ``bot.intro``）
- ``.bot off``   关闭**本群**的骰娘功能：掷骰、人物卡、日志、先攻、LLM 工具……
                 其他群聊、其他插件与 LLM 对话不受影响
- ``.bot on``    重新开启本群

群内所有成员都可以开/关，不需要管理员权限。开关按群号（私聊按会话）各自独立，
状态由 :mod:`component.bot_switch` 持久化；关闭时 router 只忽略本群的其他指令，
但仍放行 ``.bot`` 本身，方便查看状态并重新开启。
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

from ..component import bot_switch
from ..component import character as charmod
from ..component.output import get_config, get_output

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_UPTIME_MAX = 60 * 60 * 24 * 365

_plugin_meta_cache: Optional[tuple] = None

_ON_ALIASES = {"on", "enable", "start", "开启", "开"}
_OFF_ALIASES = {"off", "disable", "stop", "close", "关闭", "关"}
_INFO_ALIASES = {"", "info", "status", "about", "?", "状态", "介绍"}


def _plugin_meta() -> tuple:
    """从 metadata.yaml 读取插件名与版本号（读取失败时回退为默认值）。"""
    global _plugin_meta_cache
    if _plugin_meta_cache is not None:
        return _plugin_meta_cache

    name, version = "骰娘", ""
    try:
        import yaml

        meta_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "metadata.yaml",
        )
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        name = str(meta.get("display_name") or name)
        version = str(meta.get("version") or version)
    except Exception:
        pass

    _plugin_meta_cache = (name, version)
    return _plugin_meta_cache


def _fill(template: str, values: dict) -> str:
    """只替换已知的 ``{占位符}``，未知占位符原样保留（比 str.format 更宽容）。"""
    if not isinstance(template, str):
        return ""

    def _replace(match: "re.Match") -> str:
        key = match.group(1)
        if key in values:
            return str(values[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template)


def _format_uptime(seconds: float) -> str:
    total = max(0, min(int(seconds), _UPTIME_MAX))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天{hours}小时{minutes}分"
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


class BotMixin:

    # ------------------------- 指令入口 -------------------------
    async def bot_command(self, event, sub_cmd: str = ""):
        """`.bot` 指令实现，由 router 派发。"""
        parts = (sub_cmd or "").strip().split()
        sub = parts[0].lower() if parts else ""

        if sub in _ON_ALIASES:
            async for result in self._bot_switch_on(event):
                yield result
            return

        if sub in _OFF_ALIASES:
            async for result in self._bot_switch_off(event):
                yield result
            return

        if sub in _INFO_ALIASES:
            yield event.plain_result(self.bot_intro_text(event))
            return

        yield event.plain_result(self._render_bot_text("bot.usage"))

    async def _bot_switch_on(self, event):
        key = bot_switch.session_key(event)

        if bot_switch.is_enabled(key):
            yield event.plain_result(self._render_bot_text("bot.already_on", self._bot_values(event, True)))
            return

        bot_switch.set_enabled(key, True, updated_by=event.get_sender_id())
        yield event.plain_result(self._render_bot_text("bot.on", self._bot_values(event, True)))

    async def _bot_switch_off(self, event):
        key = bot_switch.session_key(event)

        if not bot_switch.is_enabled(key):
            yield event.plain_result(self._render_bot_text("bot.already_off", self._bot_values(event, False)))
            return

        bot_switch.set_enabled(key, False, updated_by=event.get_sender_id())
        yield event.plain_result(self._render_bot_text("bot.off", self._bot_values(event, False)))

    # ------------------------- 文本渲染 -------------------------
    def bot_intro_text(self, event) -> str:
        """`.bot` 输出的介绍文本：优先使用配置中的自定义内容。"""
        key = bot_switch.session_key(event)
        values = self._bot_values(event, bot_switch.is_enabled(key))

        custom_intro = str(get_config("bot.intro", "") or "").strip()
        template = custom_intro or get_output("bot.info")
        return _fill(template, values) or "骰娘已就绪。"

    def _bot_values(self, event, enabled: bool) -> dict:
        name, version = _plugin_meta()
        status = get_output("bot.status_on" if enabled else "bot.status_off")

        platform = "未知"
        try:
            platform = str(event.get_platform_name() or "未知")
        except Exception:
            pass

        scope = "本会话"
        try:
            if str(event.get_group_id() or "").strip():
                scope = "本群"
        except Exception:
            pass

        card = "未创建"
        try:
            chara = charmod.get_current_character(event.get_sender_id())
            if chara:
                card = str(chara.get("name") or "未创建")
        except Exception:
            pass

        started_at = getattr(self, "start_time", None)
        uptime = _format_uptime(time.time() - started_at) if started_at else "—"

        state = bot_switch.get_state(bot_switch.session_key(event))
        return {
            "name": name,
            "version": version,
            "status": status,
            "platform": platform,
            "prefix": ". / 。 / /",
            "card": card,
            "uptime": uptime,
            "scope": scope,
            "updated_at": state.get("updated_at") or "—",
            "updated_by": state.get("updated_by") or "—",
        }

    def _render_bot_text(self, key: str, values: Optional[dict] = None) -> str:
        return _fill(get_output(key), values or {})
