"""
OneBot / QQ (aiocqhttp) 平台适配器。

使用 OneBot 标准 API：
  - send_group_msg（含 reply + at）
  - send_private_msg
  - get_group_member_info
  - set_group_card
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePlatformAdapter

if TYPE_CHECKING:
    from ..astrbot_compat import AstrMessageEvent


class AiocqhttpAdapter(BasePlatformAdapter):
    """OneBot / QQ 平台的适配器实现。"""

    def get_name(self) -> str:
        return "aiocqhttp"

    @property
    def supports_recall_events(self) -> bool:
        return True

    @property
    def supports_group_card(self) -> bool:
        return True

    @property
    def supports_group_leave(self) -> bool:
        return True

    async def send_group_message(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        reply: bool = True,
    ) -> bool:
        client = event.bot
        group_id = event.get_group_id()

        # 私聊没有群号，交给调用方回退到私聊发送
        if not group_id:
            return False

        chain: list[dict] = []

        # 只引用原消息，不再 @ 发送者
        if reply and hasattr(event.message_obj, "message_id") and event.message_obj.message_id:
            chain.append({"type": "reply", "data": {"id": event.message_obj.message_id}})

        chain.append({"type": "text", "data": {"text": text}})

        try:
            await client.api.call_action("send_group_msg", **{
                "group_id": group_id,
                "message": chain,
            })
        except Exception as exc:
            print(f"[TRPGDice] 群消息发送失败: {exc}")
            return False
        return True

    async def send_private_message(
        self,
        event: AstrMessageEvent,
        text: str,
    ) -> bool:
        client = event.bot
        user_id = event.get_sender_id()
        if not user_id:
            return False

        try:
            await client.api.call_action("send_private_msg", **{
                "user_id": user_id,
                "message": [{"type": "text", "data": {"text": text}}],
            })
        except Exception as exc:
            print(f"[TRPGDice] 私聊消息发送失败: {exc}")
            return False
        return True

    async def get_nickname(
        self,
        event: AstrMessageEvent,
    ) -> str:
        client = event.bot
        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        # 私聊没有群成员信息可查，直接用平台提供的昵称
        if not group_id:
            return event.get_sender_name()

        try:
            ret = await client.api.call_action(
                "get_group_member_info",
                **{"group_id": group_id, "user_id": user_id, "no_cache": True},
            )
        except Exception:
            return event.get_sender_name()

        # ret 可能是一个 dict 或一个对象，兼容处理
        if isinstance(ret, dict):
            card = ret.get("card") or ret.get("nickname") or ""
        else:
            card = getattr(ret, "card", None) or getattr(ret, "nickname", "") or ""
        return card or event.get_sender_name()

    async def set_group_card(
        self,
        event: AstrMessageEvent,
        card: str,
    ) -> bool:
        client = event.bot
        await client.api.call_action(
            "set_group_card",
            **{
                "group_id": event.get_group_id(),
                "user_id": event.get_sender_id(),
                "card": card,
            },
        )
        return True

    async def leave_group(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        group_id = event.get_group_id()
        if not group_id:
            return False

        client = event.bot
        # is_dismiss=False：普通退群（True 是群主解散群，这里绝不能传）
        await client.api.call_action(
            "set_group_leave",
            **{"group_id": group_id, "is_dismiss": False},
        )
        return True
