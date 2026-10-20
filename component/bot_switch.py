"""
按群独立的骰娘总开关（`.bot off` / `.bot on`）。

作用域：
  - 群聊：以 ``group:<群号>`` 为键，本群开关只影响本群；
  - 私聊：以 ``private:<用户ID>`` 为键，与任何群互不影响。

实现：
  - 内存里就是一张 ``{键: 状态}`` 的字典（首次访问时从 ``data/bot_state.json`` 读入，
    切换后写回，重启 AstrBot 依然有效）；
  - 未出现过的群默认开启，可用配置 ``bot.default_enabled`` 改默认值；
  - 只拦截本插件自身的指令与日志，不拦截事件，其他插件、LLM 对话照常工作。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_PLUGIN_DIR, "data", "bot_state.json")

_lock = threading.RLock()
_cache: Optional[dict] = None


def _default_enabled() -> bool:
    """某个群从未切换过开关时的默认状态（配置 bot.default_enabled）。"""
    try:
        from .output import get_config

        return bool(get_config("bot.default_enabled", True))
    except Exception:
        return True


def session_key(event) -> str:
    """取当前会话的开关键：群聊用群号，私聊用用户 ID。"""
    group_id = ""
    try:
        group_id = str(event.get_group_id() or "").strip()
    except Exception:
        group_id = ""
    if group_id:
        return f"group:{group_id}"

    user_id = ""
    try:
        user_id = str(event.get_sender_id() or "").strip()
    except Exception:
        user_id = ""
    return f"private:{user_id}" if user_id else "default"


def _empty_state() -> dict:
    return {"version": 1, "groups": {}}


def _read_state() -> dict:
    state = _empty_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return state

    groups = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups, dict):
        return state

    for key, item in groups.items():
        if not isinstance(item, dict):
            continue
        state["groups"][str(key)] = {
            "enabled": bool(item.get("enabled", True)),
            "updated_at": item.get("updated_at"),
            "updated_by": item.get("updated_by"),
        }
    return state


def _write_state(state: dict) -> None:
    folder = os.path.dirname(STATE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = _read_state()
    return _cache


def get_state(key: str) -> dict:
    """返回某个群/会话的开关状态，字段 enabled / updated_at / updated_by。"""
    with _lock:
        item = _load()["groups"].get(key)
        if item is None:
            return {"enabled": _default_enabled(), "updated_at": None, "updated_by": None}
        return dict(item)


def is_enabled(key: str) -> bool:
    """该群/会话是否开启（键由 :func:`session_key` 生成）。"""
    return bool(get_state(key).get("enabled", True))


def set_enabled(key: str, enabled: bool, updated_by: Any = None) -> dict:
    """写入某个群/会话的开关并立即落盘，返回新的状态副本。"""
    global _cache
    with _lock:
        groups = {k: dict(v) for k, v in _load()["groups"].items()}
        groups[key] = {
            "enabled": bool(enabled),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_by": str(updated_by) if updated_by else None,
        }
        state = {"version": 1, "groups": groups}
        try:
            _write_state(state)
        except OSError:
            # 落盘失败（只读目录等）时仍然更新内存状态，避免本次操作无效
            pass
        _cache = state
        return dict(groups[key])


def reset_cache() -> None:
    """清空内存缓存（手动修改状态文件后或测试时使用）。"""
    global _cache
    with _lock:
        _cache = None
