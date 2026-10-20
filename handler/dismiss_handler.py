"""
`.dismiss` 退群指令 Mixin。

任何人（群内任意成员，无需权限）发送 ``.dismiss``，骰娘会先在群里留一句告别，
然后立即退出该群。退群能力由平台适配器的 ``leave_group()`` 提供，
目前 OneBot/QQ（aiocqhttp）与 Telegram 支持，其余平台会提示手动移除。

该指令不受 ``.bot off`` 影响：即使本群骰娘功能已关闭，也仍然可以用它退群。
"""

from __future__ import annotations

from ..component.output import get_output
from ..component.platform_adapter import get_adapter


class DismissMixin:

    async def dismiss_group(self, event):
        """`.dismiss`：立即退群，由 router 派发。"""
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            yield event.plain_result(get_output("bot.dismiss_private"))
            return

        adapter = get_adapter(event)
        if not adapter.supports_group_leave:
            yield event.plain_result(get_output("bot.dismiss_unsupported"))
            return

        # 先发告别语，再退群（退群后就没有发言权限了）
        farewell = get_output("bot.dismiss")
        if farewell:
            try:
                await adapter.send_group_message(event, farewell, reply=True)
            except Exception:
                pass

        error = ""
        try:
            left = await adapter.leave_group(event)
        except Exception as exc:
            left = False
            error = str(exc)[:200]

        if not left:
            yield event.plain_result(
                get_output("bot.dismiss_failed", error=error or "平台未返回成功")
            )
