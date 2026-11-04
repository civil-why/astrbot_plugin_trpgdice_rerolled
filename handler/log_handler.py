"""
日志管理 Mixin。

提供跑团日志的创建、暂停、恢复、结束、导出、删除和统计功能。
@filter.command_group 装饰器不会被 AstrBot 调度，实际路由见 handler/router.py。
"""

import asyncio
import os
import time

from ..component.astrbot_compat import filter, AstrMessageEvent

from ..component import illustration as illustration_mod
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

    @log.command("draw")
    async def cmd_log_draw(self, event: AstrMessageEvent):
        """把最近一段总结中的高光画成插图（默认手动触发）。

        用法：.log draw [序号|cover|all]
        """
        group = event.message_obj.group_id
        if not group:
            yield event.plain_result(get_output("log.illustration.group_only"))
            return
        if not get_config("log_illustration.enabled", True):
            yield event.plain_result(get_output("log.illustration.disabled"))
            return

        group_key = str(group)
        try:
            cooldown = max(0, int(get_config("log_illustration.cooldown_seconds", 30)))
        except (TypeError, ValueError):
            cooldown = 30
        last_draw = float(self._illustration_times.get(group_key, 0.0))
        remaining = cooldown - (time.monotonic() - last_draw)
        if cooldown > 0 and remaining > 0:
            yield event.plain_result(
                get_output("log.illustration.cooldown", seconds=int(remaining) + 1)
            )
            return

        name, sec = await self.logger_core.latest_session_with_summaries(group_key)
        summaries = (sec or {}).get("summaries") or []
        if not name or not summaries:
            yield event.plain_result(get_output("log.illustration.no_session"))
            return

        latest = summaries[-1] or {}
        summary_text = str(latest.get("summary") or "").strip()
        highlights = [
            str(item).strip()
            for item in (latest.get("highlights") or [])
            if str(item).strip()
        ]

        parts = (event.message_str or "").strip().split()
        selector = parts[2].lower() if len(parts) >= 3 else "1"

        if selector in {"cover", "封面"}:
            if not summary_text:
                yield event.plain_result(get_output("log.illustration.no_highlights"))
                return
            targets = [(0, summary_text, "cover")]
        elif selector in {"all", "全部"}:
            if not highlights:
                yield event.plain_result(get_output("log.illustration.no_highlights"))
                return
            try:
                limit = max(1, int(get_config("log_illustration.max_per_call", 3)))
            except (TypeError, ValueError):
                limit = 3
            targets = [
                (index, text, "highlight")
                for index, text in enumerate(highlights[:limit], start=1)
            ]
        else:
            if not highlights:
                yield event.plain_result(get_output("log.illustration.no_highlights"))
                return
            try:
                index = int(selector)
            except (TypeError, ValueError):
                index = 1
            if not 1 <= index <= len(highlights):
                yield event.plain_result(
                    get_output("log.illustration.index_invalid", count=len(highlights))
                )
                return
            targets = [(index, highlights[index - 1], "highlight")]

        plugin_name = str(
            get_config("log_illustration.plugin_name", illustration_mod.DEFAULT_DRAW_PLUGIN)
            or ""
        )
        draw_plugin, status = illustration_mod.find_draw_plugin(self.context, plugin_name)
        if draw_plugin is None or not status.get("chain_ready"):
            yield event.plain_result(
                get_output("log.illustration.unavailable", reason=status.get("message", ""))
            )
            return

        style_prompt = str(get_config("log_illustration.style_prompt", "") or "")
        aspect_ratio = str(get_config("log_illustration.aspect_ratio", "") or "")
        respect_permission = bool(get_config("log_illustration.respect_permission", False))
        record_usage = bool(get_config("log_illustration.record_usage", False))
        reuse_existing = bool(get_config("log_illustration.reuse_existing", True))
        try:
            timeout = max(30, int(get_config("log_illustration.timeout", 180)))
        except (TypeError, ValueError):
            timeout = 180

        if group_key in self._illustration_busy:
            yield event.plain_result(get_output("log.illustration.busy"))
            return

        stored = sec.get("illustrations") or []
        self._illustration_busy.add(group_key)
        self._illustration_times[group_key] = time.monotonic()
        try:
            for index, scene, kind in targets:
                key = illustration_mod.highlight_hash(scene)
                existing = None
                if kind == "highlight" and reuse_existing:
                    existing = next(
                        (
                            item
                            for item in stored
                            if item.get("hash") == key
                            and item.get("file")
                            and os.path.isfile(str(item["file"]))
                        ),
                        None,
                    )

                if existing is not None:
                    image_path = str(existing["file"])
                else:
                    yield event.plain_result(
                        get_output(
                            "log.illustration.generating",
                            index=index if kind == "highlight" else "封面",
                            total=len(targets),
                        )
                    )
                    prompt = illustration_mod.build_prompt(style_prompt, scene)
                    try:
                        result = await asyncio.wait_for(
                            draw_plugin.generate_images_for_plugin(
                                prompt=prompt,
                                count=1,
                                aspect_ratio=aspect_ratio,
                                event=event if respect_permission else None,
                                record_usage=record_usage,
                            ),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        yield event.plain_result(
                            get_output("log.illustration.failed", error="绘制超时")
                        )
                        return
                    except Exception as exc:
                        yield event.plain_result(
                            get_output("log.illustration.failed", error=str(exc)[:120])
                        )
                        return

                    if not isinstance(result, dict) or not result.get("success"):
                        message = str((result or {}).get("message") or "绘图插件返回失败")
                        yield event.plain_result(
                            get_output("log.illustration.failed", error=message[:120])
                        )
                        return

                    images = result.get("images") or []
                    if not images:
                        yield event.plain_result(
                            get_output("log.illustration.failed", error="没有返回图片")
                        )
                        return

                    image_meta = images[0]
                    try:
                        image_path = await illustration_mod.persist_image(
                            image_meta, group_key, name, key
                        )
                    except Exception as exc:
                        yield event.plain_result(
                            get_output("log.illustration.failed", error=str(exc)[:120])
                        )
                        return

                    try:
                        await self.logger_core.save_illustration(
                            group_key,
                            name,
                            {
                                "hash": key,
                                "index": index,
                                "kind": kind,
                                "text": scene,
                                "prompt": prompt[:1000],
                                "file": image_path,
                                "provider": str(image_meta.get("provider_id") or ""),
                                "model": str(image_meta.get("model") or ""),
                                "time": int(time.time()),
                            },
                        )
                        stored = sec.get("illustrations") or stored
                    except Exception:
                        pass

                caption = (
                    get_output("log.illustration.cover", summary=scene)
                    if kind == "cover"
                    else get_output("log.illustration.result", index=index, text=scene)
                )
                try:
                    from astrbot.api.message_components import Image, Plain

                    yield event.chain_result(
                        [Plain(caption + "\n"), Image.fromFileSystem(image_path)]
                    )
                except Exception as exc:
                    yield event.plain_result(
                        get_output(
                            "log.illustration.failed",
                            error=f"发送图片失败: {str(exc)[:80]}",
                        )
                    )
                    return
        finally:
            self._illustration_busy.discard(group_key)

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
        if ok:
            try:
                illustration_mod.clear_illustrations(group, name)
            except Exception:
                pass
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
