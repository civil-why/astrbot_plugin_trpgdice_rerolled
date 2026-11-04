"""高光时刻配图：探测绘图插件并把高光文本渲染成插图。

模块只依赖 AstrBot 公共接口和骰娘自己的数据目录：
  * 通过 ``context.get_registered_star()`` 取得万象画卷插件实例；
  * 调用它的 ``generate_images_for_plugin()`` 拿到图片（该接口不会自动下发）；
  * 图片统一复制到 ``data/highlight_images/<群>/<日志>/``，避免万象画卷
    清理临时缓存后图片失效。
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import shutil
from typing import Any, Dict, Optional, Tuple

import aiohttp

DEFAULT_DRAW_PLUGIN = "astrbot_plugin_omnidraw"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
FALLBACK_STYLE = "剧情插画，画面细节丰富，构图完整"

_COMPONENT_DIR = os.path.dirname(os.path.abspath(__file__))
ILLUSTRATION_ROOT = os.path.normpath(
    os.path.join(_COMPONENT_DIR, "..", "data", "highlight_images")
)
_UNSAFE_PATH_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def find_draw_plugin(
    context: Any,
    plugin_name: str = "",
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """探测万象画卷插件实例与可用状态。

    Args:
        context: AstrBot 插件上下文，需提供 ``get_registered_star``。
        plugin_name: 绘图插件名，留空时使用 ``DEFAULT_DRAW_PLUGIN``。

    Returns:
        ``(插件实例或 None, 状态字典)``。状态字典包含 installed、activated、
        api_available、chain_ready、optimizer_enabled 等字段，可直接用于后台展示。
    """
    name = str(plugin_name or "").strip() or DEFAULT_DRAW_PLUGIN
    status: Dict[str, Any] = {
        "plugin_name": name,
        "installed": False,
        "activated": False,
        "api_available": False,
        "chain_nodes": 0,
        "chain_ready": False,
        "optimizer_enabled": False,
        "optimizer_style": "",
        "message": "",
    }

    try:
        metadata = context.get_registered_star(name)
    except Exception:
        metadata = None

    if metadata is None:
        status["message"] = f"未安装绘图插件 {name}"
        return None, status

    status["installed"] = True
    status["activated"] = bool(getattr(metadata, "activated", False))
    star = getattr(metadata, "star_cls", None)
    if not status["activated"] or star is None:
        status["message"] = "绘图插件当前未启用"
        return None, status

    status["api_available"] = callable(
        getattr(star, "generate_images_for_plugin", None)
    )
    if not status["api_available"]:
        status["message"] = "绘图插件版本过旧，缺少插件调用接口"
        return None, status

    config = getattr(star, "plugin_config", None)
    if config is not None:
        chain = list(getattr(config, "chains", {}).get("text2img", []) or [])
        get_provider = getattr(config, "get_provider", None)
        status["chain_nodes"] = len(chain)
        for provider_id in chain:
            try:
                provider = get_provider(provider_id) if callable(get_provider) else None
            except Exception:
                provider = None
            if provider is None:
                continue
            has_key = bool(getattr(provider, "has_api_key", False))
            is_gemini = str(getattr(provider, "api_type", "") or "") == "gemini_official"
            has_target = bool(getattr(provider, "base_url", "")) and bool(
                getattr(provider, "model", "")
            )
            if has_key and (has_target or is_gemini):
                status["chain_ready"] = True
                break
        status["optimizer_enabled"] = bool(getattr(config, "enable_optimizer", False))
        status["optimizer_style"] = str(getattr(config, "optimizer_style", "") or "")

    if not status["chain_ready"]:
        status["message"] = "绘图插件缺少可用的画图节点"
        return star, status

    status["message"] = "绘图插件已就绪"
    return star, status


def build_prompt(style_prompt: str, scene: str) -> str:
    """把高光文本拼成一条绘图提示词。

    Args:
        style_prompt: 用户手动配置的画风基准。
        scene: 高光片段或剧情概括。

    Returns:
        单行提示词，已压缩空白并限制长度。
    """
    style = " ".join(str(style_prompt or "").split()) or FALLBACK_STYLE
    body = " ".join(str(scene or "").split())[:600]
    return (
        f"{style}。TRPG 跑团剧情插图，画面内容：{body}。"
        "要求：单一场景、构图完整、人物与动作清晰；"
        "画面中不要出现任何文字、字幕、水印、logo 或界面元素。"
    )


def highlight_hash(text: str) -> str:
    """返回高光文本的短哈希，用于复用已生成的插图。"""
    normalized = " ".join(str(text or "").split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _safe_segment(value: str, fallback: str) -> str:
    """清理路径片段，防止路径穿越与非法字符。"""
    cleaned = _UNSAFE_PATH_RE.sub("", str(value or "")).replace("..", "").strip()
    return (cleaned or fallback)[:64]


def illustration_dir(group_id: str, session_name: str) -> str:
    """返回某个日志会话的插图目录。"""
    return os.path.join(
        ILLUSTRATION_ROOT,
        _safe_segment(group_id, "group"),
        _safe_segment(session_name, "session"),
    )


def _guess_extension(image: Dict[str, Any]) -> str:
    content_type = str(image.get("content_type") or "").split(";")[0].strip()
    extension = mimetypes.guess_extension(content_type) if content_type else ""
    if extension in _IMAGE_EXTENSIONS:
        return ".jpg" if extension == ".jpeg" else extension

    for field in ("file_path", "url", "image_url"):
        value = str(image.get(field) or "").split("?", 1)[0]
        extension = os.path.splitext(value)[1].lower()
        if extension in _IMAGE_EXTENSIONS:
            return ".jpg" if extension == ".jpeg" else extension
    return ".png"


async def persist_image(
    image: Dict[str, Any],
    group_id: str,
    session_name: str,
    key: str,
) -> str:
    """把绘图结果保存到插件数据目录。

    Args:
        image: ``generate_images_for_plugin`` 返回的单张图片字典。
        group_id: 群号。
        session_name: 日志会话名。
        key: 文件名（通常是高光哈希）。

    Returns:
        本地图片的绝对路径。

    Raises:
        RuntimeError: 结果里没有可用图片数据，或图片超过大小限制。
    """
    dest_dir = illustration_dir(group_id, session_name)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{_safe_segment(key, 'image')}{_guess_extension(image)}")

    file_path = str(image.get("file_path") or "")
    if file_path and os.path.isfile(file_path):
        shutil.copyfile(file_path, dest_path)
        return dest_path

    data = b""
    data_url = str(image.get("data_url") or "")
    if data_url.startswith("data:image"):
        payload = data_url.partition(",")[2]
        data = base64.b64decode(payload + "=" * (-len(payload) % 4))

    if not data:
        url = str(image.get("url") or "")
        if not url and str(image.get("image_url") or "").startswith("http"):
            url = str(image["image_url"])
        if url.startswith(("http://", "https://")):
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=60)
                async with session.get(url, timeout=timeout) as response:
                    if response.status != 200:
                        raise RuntimeError(f"下载图片失败: HTTP {response.status}")
                    data = await response.read()

    if not data:
        raise RuntimeError("绘图结果中没有可用的图片数据")
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError("图片超过大小限制")

    with open(dest_path, "wb") as file:
        file.write(data)
    return dest_path


def clear_illustrations(group_id: str, session_name: str) -> None:
    """删除某个日志会话的全部插图（删除日志时调用）。"""
    shutil.rmtree(illustration_dir(group_id, session_name), ignore_errors=True)
