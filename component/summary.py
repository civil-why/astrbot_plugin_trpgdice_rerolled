"""
跑团日志的阶段总结与高光提取（纯逻辑，不依赖 AstrBot 运行时）。

由 ``.log off`` 触发：把本段日志整理成「昵称: 内容」的对话记录，交给 LLM
输出一个 JSON，包含：
  - summary    : 150~300 字的阶段性剧情概括
  - highlights : 2~5 条令人印象深刻的高光片段（供后续绘图插件使用）
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Tuple

SYSTEM_PROMPT = (
    "你是一位经验丰富的 TRPG 记录员。用户会给你一段跑团聊天记录，每行格式为「昵称: 内容」。\n"
    "请完成两件事：\n"
    "1. 用 150~300 字概括这段故事：发生了什么、涉及哪些角色、目标与冲突、结果或悬念；\n"
    "2. 挑出 2~5 个最令人印象深刻的高光片段（戏剧性转折、精彩操作、名场面、爆笑或震撼瞬间），"
    "每条 20~40 字，写清是谁做了什么。\n"
    "只输出一个 JSON 对象，不要 Markdown 代码块，不要额外解释：\n"
    '{"summary": "……", "highlights": ["……", "……"]}'
)

# 以命令前缀开头的短消息视为指令，不参与总结
_COMMAND_PREFIX_RE = re.compile(r"^[.。/]")
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)
_HIGHLIGHT_PREFIX_RE = re.compile(r"^\s*(?:[-*•·]|\d+\s*[.、)])\s*")


def is_command_text(text: str) -> bool:
    return bool(_COMMAND_PREFIX_RE.match((text or "").strip()))


def build_transcript(
    messages: Iterable[dict],
    max_chars: int = 8000,
    include_dice: bool = False,
) -> str:
    """把日志消息整理成「昵称: 内容」形式的纯文本。

    - 跳过空消息、掷骰结果（默认）和短指令；
    - 超长时保留最近的内容，并在开头标注「前略」。
    """
    lines: List[str] = []
    for message in messages or []:
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if message.get("isDice") and not include_dice:
            continue
        if is_command_text(text) and len(text) <= 60:
            continue

        text = " ".join(text.split())
        nickname = str(message.get("nickname") or "调查员").strip() or "调查员"
        lines.append(f"{nickname}: {text}")

    transcript = "\n".join(lines)
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = 8000
    if limit > 0 and len(transcript) > limit:
        transcript = "（前略）\n" + transcript[-limit:]
    return transcript


def build_user_prompt(transcript: str, max_highlights: int = 5) -> str:
    return (
        "以下是本段跑团记录：\n\n"
        f"{transcript}\n\n"
        f"请按要求输出 JSON（高光最多 {max(1, int(max_highlights))} 条）。"
    )


def _clean_highlight(text: Any) -> str:
    value = _HIGHLIGHT_PREFIX_RE.sub("", str(text or "").strip())
    return value.strip().strip('"').strip()


def parse_summary(raw: str, max_highlights: int = 5) -> Tuple[str, List[str]]:
    """解析模型输出，返回 (summary, highlights)。

    兼容 ```json 代码块、前后多余文字、highlight 为字符串或 dict 等情况；
    完全解析失败时把整段文本当作 summary。
    """
    text = (raw or "").strip()
    if not text:
        return "", []

    text = _CODE_FENCE_RE.sub("", text).strip()

    data: Any = None
    block = _JSON_BLOCK_RE.search(text)
    if block:
        try:
            data = json.loads(block.group(0))
        except (ValueError, TypeError):
            data = None
    if not isinstance(data, dict):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None

    if isinstance(data, dict):
        summary = str(data.get("summary") or "").strip()
        raw_highlights = data.get("highlights") or []
        highlights: List[str] = []
        if isinstance(raw_highlights, list):
            for item in raw_highlights:
                if isinstance(item, dict):
                    item = (
                        item.get("text")
                        or item.get("content")
                        or item.get("highlight")
                        or item.get("title")
                        or ""
                    )
                cleaned = _clean_highlight(item)
                if cleaned:
                    highlights.append(cleaned)
        try:
            limit = max(1, int(max_highlights))
        except (TypeError, ValueError):
            limit = 5
        return summary, highlights[:limit]

    # 模型没按要求输出 JSON：整段当总结
    return text[:2000], []


def render_highlights(highlights: List[str], line_template: str, empty_text: str = "") -> str:
    """把高光列表渲染成多行文本。"""
    lines = []
    for index, text in enumerate(highlights, start=1):
        try:
            lines.append(line_template.format(index=index, text=text))
        except (KeyError, IndexError, ValueError):
            lines.append(f"{index}. {text}")
    return "\n".join(lines) if lines else empty_text
