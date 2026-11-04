"""LLM 美化：core 固定，只允许模型写前后缀。

core 由插件生成（玩家名、骰点、判定结果，包含多重射击/奖励惩罚骰等格式）；
LLM 只返回 JSON：{"prefix": "...", "suffix": "..."}。
最终输出 = prefix + core + suffix，core 永远不会被模型改写。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

DEFAULT_DECORATE_PROMPT = (
    "你是跑团骰娘，风格活泼开朗。"
    "你会收到一段 core，包含玩家名称、骰点数值和判定结果，"
    "其中可能有多重射击、奖励骰或惩罚骰等格式。"
    "你只负责在 core 前后写 prefix / suffix 作为语气、动作或转场，"
    "不能修改、删减或重复 core 本身。"
    "不要重复 core 里的数字、骰子表达式或判定标签。"
)

OUTPUT_PROTOCOL = (
    "【输出协议】只输出一个 JSON 对象，不要输出 Markdown 代码块或解释：\n"
    '{"prefix":"放在 core 前面的文字，可以为空","suffix":"放在 core 后面的文字，可以为空"}'
)

# 旧版默认提示词：用于识别并迁移旧配置，避免旧提示词把 JSON 协议带偏。
LEGACY_REWRITE_PROMPT = (
    "你是一个跑团骰娘，风格活泼可爱。"
    "请用简短生动的语言（不超过60字）描述以下掷骰结果，"
    "可以加入一点点角色扮演风格，但必须保留所有数字和判定结论。"
)

MAX_DECORATION_CHARS = 300

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


@dataclass
class Decoration:
    prefix: str = ""
    suffix: str = ""

    def apply(self, core: str) -> str:
        return f"{self.prefix}{core}{self.suffix}"


def select_system_prompt(mode: str, configured_prompt: str) -> str:
    """按模式选择提示词，并自动迁移旧版默认提示词。"""
    mode = mode if mode in ("decorate", "rewrite") else "decorate"
    configured = (configured_prompt or "").strip()

    if mode == "rewrite":
        if not configured or configured == DEFAULT_DECORATE_PROMPT:
            return LEGACY_REWRITE_PROMPT
        return configured

    if not configured or configured == LEGACY_REWRITE_PROMPT:
        return DEFAULT_DECORATE_PROMPT
    return configured


def ensure_protocol(system_prompt: str) -> str:
    """给任意自定义提示词补上固定 JSON 输出协议。"""
    prompt = (system_prompt or "").strip() or DEFAULT_DECORATE_PROMPT
    if "【输出协议】" in prompt:
        return prompt
    return f"{prompt}\n\n{OUTPUT_PROTOCOL}"


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = _CODE_FENCE_RE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _clean_part(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:MAX_DECORATION_CHARS]


def parse_decoration(text: str) -> Decoration | None:
    """解析模型返回的 JSON；不合法时返回 None，由调用方回退 core。"""
    payload = _extract_json(text)
    if payload is None:
        return None

    prefix = _clean_part(payload.get("prefix", ""))
    suffix = _clean_part(payload.get("suffix", ""))
    if prefix is None or suffix is None:
        return None
    return Decoration(prefix=prefix, suffix=suffix)


def assemble_decoration(core: str, model_output: str) -> str:
    """把模型前后缀拼到固定 core 上；解析失败时原样返回 core。"""
    decoration = parse_decoration(model_output)
    if decoration is None:
        return core
    return decoration.apply(core)
