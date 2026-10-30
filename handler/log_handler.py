"""
日志管理 Mixin。

提供跑团日志的创建、暂停、恢复、结束、导出、删除和统计功能。
@filter.command_group 装饰器不会被 AstrBot 调度，实际路由见 handler/router.py。
"""

import asyncio

from ..component.astrbot_compat import filter, AstrMessageEvent

from ..component import summary as summary_mod
from ..component.output import get_config, get_output


class LogMixin:

    @filter.command_group("log")
    async def log(self, event: AstrMessageEvent):
        pass

    @log.command("new")
    async def cmd_log_new(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        ok, info = await self.logger_core.new_session(group, name)
        yield event.plain_result(info)

    @log.command("end")
    async def cmd_log_end(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        ok, result = await self.logger_core.end_session(group)

        if not ok:
            yield event.plain_result(result)
            return

        name, sec = result

        try:
            from astrbot.api.message_components import File

            ok, file_path = await self.logger_core.export_session(group, sec, name)
            if not ok:
                yield event.plain_result(get_output("log.export_failed", error=file_path))
                return

            file_name = f"{group}_{name}.json"
            yield event.chain_result([File(file=file_path, name=file_name)])
            yield event.plain_result(
                get_output("log.session_exported", session_name=name, file_name=file_name)
            )
        except Exception as e:
            yield event.plain_result(
                get_output("log.send_file_failed", error=str(e))
            )

    @log.command("off")
    async def cmd_log_off(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        ok, info = await self.logger_core.pause_sessions(group)
        yield event.plain_result(info)
        if not ok:
            return
        # 暂停成功后生成本段小结；失败不影响暂停状态
        async for result in self.generate_log_summary(event, group):
            yield result

    async def generate_log_summary(self, event: AstrMessageEvent, group_id: str):
        """.log off 后总结本段剧情并提取高光。"""
        if not get_config("log_summary.enabled", True):
            return

        name, sec = await self.logger_core.latest_paused_session(group_id)
        if not sec:
            return

        messages = sec.get("messages", []) or []
        try:
            cursor = max(0, int(sec.get("summary_cursor", 0) or 0))
        except (TypeError, ValueError):
            cursor = 0
        segment = messages[cursor:]

        transcript = summary_mod.build_transcript(
            segment,
            max_chars=get_config("log_summary.max_chars", 8000),
            include_dice=get_config("log_summary.include_dice", False),
        )
        if not transcript.strip():
            yield event.plain_result(get_output("log.summary.empty"))
            return

        provider = self._get_summary_provider(event)
        if provider is None:
            yield event.plain_result(
                get_output("log.summary.failed", error="没有可用的对话模型 Provider")
            )
            return

        model = str(get_config("log_summary.model", "") or "").strip() or None
        try:
            timeout = max(10, int(get_config("log_summary.timeout", 60)))
        except (TypeError, ValueError):
            timeout = 60

        generating = get_output("log.summary.generating")
        if generating:
            yield event.plain_result(generating)

        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=summary_mod.build_user_prompt(
                        transcript,
                        max_highlights=get_config("log_summary.max_highlights", 5),
                    ),
                    contexts=[],
                    system_prompt=summary_mod.SYSTEM_PROMPT,
                    model=model,
                ),
                timeout=timeout,
            )
            raw = getattr(resp, "completion_text", "") or ""
        except Exception as exc:
            yield event.plain_result(
                get_output("log.summary.failed", error=str(exc)[:120] or "请求超时")
            )
            return

        summary_text, highlights = summary_mod.parse_summary(
            raw, max_highlights=get_config("log_summary.max_highlights", 5)
        )
        if not summary_text:
            yield event.plain_result(
                get_output("log.summary.failed", error="模型没有返回有效总结")
            )
            return

        try:
            await self.logger_core.save_session_summary(
                group_id, name, summary_text, highlights, cursor=len(messages)
            )
        except Exception:
            pass

        highlight_text = summary_mod.render_highlights(
            highlights,
            get_output("log.summary.highlight_line"),
            get_output("log.summary.no_highlights"),
        )
        yield event.plain_result(
            get_output(
                "log.summary.result",
                session_name=name,
                summary=summary_text,
                highlights=highlight_text,
            )
        )

    def _get_summary_provider(self, event: AstrMessageEvent):
        """优先使用单独配置的 Provider，否则回退当前对话模型。"""
        provider_id = str(get_config("log_summary.provider_id", "") or "").strip()
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

    @log.command("on")
    async def cmd_log_on(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        ok, info = await self.logger_core.resume_session(group, name)
        yield event.plain_result(info)

    @log.command("list")
    async def cmd_log_list(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        lines = await self.logger_core.list_sessions(group)
        yield event.plain_result("\n".join(lines))

    @log.command("del")
    async def cmd_log_del(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        if len(parts) < 3:
            yield event.plain_result(get_output("log.delete_error"))
            return
        name = parts[2]
        ok, info = await self.logger_core.delete_session(group, name)
        yield event.plain_result(info)

    @log.command("get")
    async def cmd_log_get(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        grp = await self.logger_core.load_group(group)
        if name not in grp:
            yield event.plain_result(
                get_output("log.session_not_found", session_name=name)
            )
            return
        sec = grp[name]
        ok, file_path = await self.logger_core.export_session(group, sec, name)
        if ok:
            try:
                from astrbot.api.message_components import File
                file_name = f"{group}_{name}.json"
                yield event.chain_result([File(file=file_path, name=file_name)])
            except Exception as e:
                yield event.plain_result(get_output("log.send_file_failed", error=str(e)))
        else:
            yield event.plain_result(get_output("log.export_failed", error=file_path))

    @log.command("stat")
    async def cmd_log_stat(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        all_flag = len(parts) >= 4 and parts[3] == "--all"
        lines = await self.logger_core.stat_sessions(group, name, all_flag)
        yield event.plain_result("\n".join(lines))
