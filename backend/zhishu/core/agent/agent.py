"""智枢智能体 —— Agent 核心循环（ReAct + 流式输出）。

  流程：
    1. 组装上下文：系统提示（分层：stable 身份 + volatile 技能/记忆/知识库）
       + 会话记忆（可选经 ContextEngine 压缩）+ （可选）RAG 检索增强。
    2. 调用 LLM 推理（支持 moa 多智能体 facade / 国产 Provider 回退链）。
    3. 若返回 tool_calls → 经工具注册中心执行（沙箱 + 出网隔离 + 审计）→ 回填 → 循环。
    4. 若为最终回答 → 流式返回 token，结束。
    5. 全过程通过异步生成器 yield 结构化事件，供 SSE 推送。

  本文件对应 Hermes 的 `AIAgent` + `run_conversation()`；把「系统提示组装」「上下文
  压缩」「多智能体」下沉到同包下的 system_prompt / context_engine / moa 模块。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Optional

from ..providers.client import LLMClient
from ..tools import ToolRegistry, ToolContext, set_current_user
from ..rag import KnowledgeBase
from ..memory import MemoryStore, MemoryManager
from ..modules import build_agent_context_prompt
from ..agents_runtime import (
    DELEGATE_TOOL_NAME,
    agent_owner,
    get_agent_meta,
    is_enabled,
    resolve_tools,
    build_agent_system_prompt,
)
from ..modules.runtime import can_view, filter_tool_specs
from ..config import ZhishuConfig, classify_model
from .system_prompt import build_system_prompt
from .context_engine import NoOpContextEngine, ContextEngine, CompressionContextEngine
from ..modules.skills import maybe_learn, maybe_reflect
from .. import image_routing

MAX_STEPS = 16


class Agent:
    def __init__(
        self,
        cfg: ZhishuConfig,
        llm: LLMClient,
        kb: Optional[KnowledgeBase] = None,
        memory: Optional[MemoryStore] = None,
        ctx: Optional[ToolContext] = None,
        media: Optional = None,
        context_engine: Optional[ContextEngine] = None,
        memory_manager: Optional[MemoryManager] = None,
    ):
        self.cfg = cfg
        self.llm = llm
        self.kb = kb
        self.memory = memory
        self.ctx = ctx or ToolContext(kb=kb, security=cfg.security)
        self.media = media
        self.context_engine = context_engine or NoOpContextEngine()
        # 外部长期记忆（向量 provider，opt-in）。为 None 时 prefetch/sync 全为 no-op，零回归。
        self.memory_manager = memory_manager

    async def run(
        self,
        user_message: str,
        session: str = "default",
        model: Optional[str] = None,
        image: Optional[str] = None,
        owner: Optional[str] = None,
        agent_name: Optional[str] = None,
        attachments: Optional[list] = None,
        is_admin: bool = False,
        user_role: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        # 让工具（知识库/文件等）能感知当前用户，按归属隔离文档。
        # 安全（防并发串号）：绝不在共享单例 ToolContext 上就地改 user ——
        # 并发请求会互相覆盖身份。这里派生**本次运行专用**的副本，并把身份
        # 写入 contextvars（task-local），owner 为空时 fail-closed 落到 anonymous，
        # 绝不继承上一个请求的身份。
        self.ctx = self.ctx.for_run(owner, session, is_admin=is_admin, user_role=user_role)
        set_current_user(self.ctx.user, is_admin=is_admin, user_role=user_role)
        # 按用户隔离模型：把 cfg/llm 收窄为「当前 owner 可见」的配置副本
        # （仅本人 + 共享 Provider + 角色命中 Provider 及其默认模型）。admin 视为可见全部，返回原 cfg。
        # 注意：Agent 实例为每轮请求独立创建，此处改写 self.cfg/self.llm 不会产生并发串号。
        if owner:
            self.cfg = self.cfg.for_user(owner, is_admin, user_role=user_role)
            self.llm = LLMClient(self.cfg, self.llm.api_mode)
        # ---- 0. 按模型类型分流：图像 / 视频 走生成分支，文本走 ReAct 循环 ----
        try:
            pc, mdl = self.cfg.resolve_model(model)
            kind = classify_model(mdl)
        except Exception:
            pc, mdl, kind = None, None, "text"

        if image and kind == "text" and pc is not None:
            alt = self._first_model_of_kind(pc, "image")
            if alt:
                mdl, kind = alt, "image"

        if kind == "image" and pc is not None:
            if image:
                mdl = self._pick_img2img_model(pc, mdl)
            async for ev in self._run_image(user_message, session, pc, mdl, image=image):
                yield ev
            return
        if kind == "video" and pc is not None:
            async for ev in self._run_video(user_message, session, pc, mdl, image=image):
                yield ev
            return

        # ---- 1. 组装上下文（分层系统提示）----
        # 先发一个状态事件保活 SSE：知识库检索（向量相似度）/ 系统提示组装可能耗时
        # （尤其首轮需惰性加载数 GB 向量索引）。若首字节迟迟不发，前端 / 反向代理的读
        # 超时会被触发，浏览器侧表现为「network error」。提前 yield 可避免该假死。
        yield {"type": "status", "text": "正在检索知识库并组织上下文…"}
        # 上下文组装涉及同步的 KB 检索与分词，放到线程池执行，避免阻塞事件循环与 SSE 推送。
        try:
            system = await asyncio.to_thread(
                build_system_prompt,
                self.cfg, agent_name=agent_name, owner=owner,
                kb=self.kb, query=user_message, is_admin=is_admin, user_role=user_role,
            )
        except Exception as e:
            # 上下文组装异常（知识库/分词等）若冲出生成器会掐断 SSE → 浏览器报 network error。
            yield {"type": "error", "message": f"上下文组装失败：{e}。请检查知识库配置后重试。"}
            yield {"type": "done"}
            return

        # 长期记忆召回（向量记忆 opt-in；无外部 provider 时返回空串，零回归）。
        # 放在系统提示末尾作为 volatile 上下文，不污染身份（stable）部分。
        if self.memory_manager is not None:
            try:
                mem_ctx = await asyncio.to_thread(
                    self.memory_manager.prefetch, owner, user_message
                )
            except Exception:
                mem_ctx = ""
            if mem_ctx:
                system = system + "\n\n" + mem_ctx

        messages = [{"role": "system", "content": system}]
        if self.memory:
            history = self.memory.history(session, limit=20)
            # 上下文压缩（ContextEngine 可插拔；NoOp 时原样返回）。
            # compress_history 为异步方法（会调用 LLM 摘要），必须在事件循环中 await。
            history = await self.context_engine.compress_history(session, history)
            for h in history:
                c = h["content"]
                # 历史消息若含视觉图片部件（列表型 content），仅保留文本部分，
                # 避免后续每轮都把已发送过的巨幅 base64 图片重复塞给模型（Hermes 同理按发送次附加）。
                if isinstance(c, list):
                    c = [p for p in c if p.get("type") != "image_url"]
                messages.append({"role": h["role"], "content": c})
        # ---- 1.5 多模态附件（对标 Hermes 图片/PDF 处理：native 视觉 / 上下文提示）----
        # 默认纯文本；当存在视觉部件时改为 content 列表 [text, image_url...]。
        user_content = user_message
        if attachments and kind == "text":
            try:
                user_content = self._build_multimodal_content(
                    user_message, attachments, mdl)
            except Exception as e:
                # 图片/附件多模态内容组装异常（转码、读取等）若冲出生成器会掐断 SSE
                # → 浏览器报 network error。转为清晰错误事件。
                yield {"type": "error", "message": f"图片/附件处理失败：{e}。请确认文件未损坏或格式受支持。"}
                yield {"type": "done"}
                return
        messages.append({"role": "user", "content": user_content})

        if self.memory:
            self.memory.append(session, "user", user_message)
        if self.memory_manager is not None:
            await self._sync_memory(owner, "user", user_message)

        # ---- LLM 可用性兜底：无可用 Provider / 未配置模型 → 优雅报错而非掐断 SSE ----
        # 否则异常会冲出 SSE 生成器，导致响应流被中断，浏览器侧表现为「network error」。
        if self.llm is None:
            yield {"type": "error", "message":
                   "未配置可用的 LLM（没有任何 enabled 的 Provider）。请先在配置中启用 Provider 并填写 "
                   "API Key，或启动本地推理服务（Ollama / vLLM）后重试。"}
            yield {"type": "done"}
            return

        # 工具集：子智能体按 tools 字段裁剪；主管使用全部（含委派工具）。
        # 多用户隔离：plugin__/mcp__ 工具按归属过滤（共享 + 本人；admin 全量），
        # 防止 A 用户的 Agent 调用 B 用户的私有插件/MCP 工具。
        if agent_name:
            specs = resolve_tools(get_agent_meta(agent_name).get("tools", "all"),
                                  username=owner, is_admin=is_admin, user_role=user_role)
            specs = [s for s in specs if s["function"]["name"] != DELEGATE_TOOL_NAME]
            try:
                max_steps = int(get_agent_meta(agent_name).get("max_steps") or MAX_STEPS)
            except (TypeError, ValueError):
                max_steps = MAX_STEPS
        else:
            specs = filter_tool_specs(ToolRegistry.specs(), owner, is_admin, user_role)
            max_steps = MAX_STEPS

        # ---- MoA 多智能体 facade：把单轮对话路由到并行聚合 ----
        if pc is not None and getattr(pc, "mode", "") == "moa":
            from .moa import MoAClient

            yield {"type": "status", "text": "正在以多智能体(MoA)模式协同作答…"}
            answer = ""
            async for piece in MoAClient(self.cfg).stream(
                messages, model=model, tools=specs if specs else None
            ):
                answer += piece
                yield {"type": "token", "text": piece}
            if self.memory:
                self.memory.append(session, "assistant", answer)
            if self.memory_manager is not None:
                await self._sync_memory(owner, "assistant", answer)
            yield {"type": "done"}
            return

        # ---- 2. 推理循环 ----
        # 轨迹采集（供技能自进化闭环使用）：记录工具调用与累计步数/工具数。
        traj_tools: list[dict] = []
        tool_total = 0
        for step in range(max_steps):
            try:
                resp = await self.llm.chat(messages, model=model, tools=specs if specs else None)
                # 防御：上游网关/代理可能返回「200 + 错误 JSON」而非抛异常，
                # 归一化后缺少 choices；此处显式校验，避免下方 resp["choices"] 触发 KeyError
                # 冲出 SSE 生成器（浏览器侧表现为「network error」）。
                if not isinstance(resp, dict) or "choices" not in resp:
                    raise RuntimeError(f"模型返回非预期响应：{str(resp)[:200]}")
                choice = resp["choices"][0]["message"]
            except Exception as e:
                # Provider 全部不可用 / 网络不可达 / 鉴权失败 / 响应畸形等：转为清晰错误事件，
                # 避免异常冲出 SSE 生成器导致响应流中断（浏览器侧表现为「network error」）。
                yield {"type": "error",
                       "message": f"模型服务调用失败：{e}。请检查 LLM Provider 配置（API Key / base_url）"
                                  f"或本地推理服务（Ollama / vLLM）是否可用。"}
                yield {"type": "done"}
                return
            tool_calls = choice.get("tool_calls")

            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield {"type": "tool_call", "name": name, "args": args}
                    if name == DELEGATE_TOOL_NAME and agent_name is None:
                        result = ""
                        async for ev in self._run_delegate(args, session, owner, is_admin):
                            if ev.get("type") == "delegate_end":
                                # 捕获最终结果用于主消息，同时将 delegate_end 转发给前端
                                # 以关闭委派 UI（与 delegate_start 对称）
                                result = ev.get("result", "")
                            yield ev
                        yield {"type": "tool_result", "name": name, "result": result,
                               "agent": args.get("agent_name") or args.get("agent")}
                        messages.append({
                            "role": "assistant",
                            "content": choice.get("content"),
                            "tool_calls": tool_calls,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": result,
                        })
                        continue
                    result = await ToolRegistry.execute(name, args, self.ctx)
                    # 上下文压缩：单条工具结果过长时摘要，降低单轮上下文占用
                    # （直接缓解长文档读取导致的 MAX_STEPS 耗尽）。仅当开启压缩且超阈值。
                    if (self.cfg.agent.compression_enabled
                            and self.cfg.agent.compression_tool_result_max > 0
                            and len(result) > self.cfg.agent.compression_tool_result_max):
                        try:
                            result = await CompressionContextEngine.compress_tool_result(
                                result, self.llm, self.cfg,
                                max_chars=self.cfg.agent.compression_tool_result_max)
                        except Exception:
                            pass
                    tool_total += 1
                    traj_tools.append({"name": name, "args": args, "result": result[:300]})
                    yield {"type": "tool_result", "name": name, "result": result}
                    # 会话内 nudge（对标 Hermes nudge 机制）：每完成 nudge_interval 次工具
                    # 调用，注入一条内部系统提醒，提示模型把可复用工作流沉淀为长期记忆/技能。
                    # 仅作软提示，不阻断主流程；0=关闭。
                    _ni = getattr(self.cfg.agent, "nudge_interval", 0) or 0
                    if _ni > 0 and tool_total % _ni == 0:
                        messages.append({
                            "role": "system",
                            "content": "[内部提醒] 你已连续完成多步工具调用。若过程中发现了"
                                       "可跨会话复用的用户偏好、项目事实、约定或踩坑经验，请用 "
                                       "memory 工具保存到长期记忆；若形成了一套可复用的方法论，"
                                       "可触发技能自学习将其沉淀为技能。",
                        })
                    # run.failed：工具执行失败（缺依赖/越权/异常）时静默记录，
                    # 供前端「run.failed 静默吞错检测」使用，不阻断对话主流程。
                    if result.startswith("[工具错误]") or result.startswith("[工具执行异常]"):
                        yield {"type": "run_failed", "name": name, "message": result}
                    messages.append({
                        "role": "assistant",
                        "content": choice.get("content"),
                        "tool_calls": tool_calls,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result,
                    })
                continue

            final = choice.get("content", "")
            if not final:
                # 模型未返回文本但已执行工具：基于轨迹生成可见的「完成摘要」，
                # 避免聊天窗口表现为「无结果 / 没回复」（典型场景：上传大文档→建技能后
                # 模型因上下文过满而空回复，工作其实已完成，用户却看不到任何反馈）。
                if tool_total > 0:
                    bits = [f"- {t['name']}: {t['result'][:160]}" for t in traj_tools[-3:]]
                    summary = (
                        f"已为你完成操作（本次共执行 {tool_total} 个工具步骤）：\n"
                        + "\n".join(bits)
                        + "\n\n（模型未给出文字总结，以上为工具执行轨迹摘要。）"
                    )
                    yield {"type": "token", "text": summary}
                    if self.memory:
                        self.memory.append(session, "assistant", summary)
                    if self.memory_manager is not None:
                        try:
                            await self._sync_memory(owner, "assistant", summary)
                        except Exception:
                            pass
                    yield {"type": "done"}
                    return
                yield {"type": "done", "note": "模型未返回内容"}
                return
            collected = []
            async for piece in self.llm.stream(messages, model=model, tools=None):
                if piece.startswith("\u0000TOOLCALL"):
                    continue
                yield {"type": "token", "text": piece}
                collected.append(piece)
            answer = "".join(collected) or final
            if self.memory:
                self.memory.append(session, "assistant", answer)
            if self.memory_manager is not None:
                await self._sync_memory(owner, "assistant", answer)
            # 技能自进化闭环：复杂任务（步数/工具数达标）完成后，异步由 LLM 蒸馏沉淀为技能文件。
            # 用 create_task 触发，不阻塞流式 done 事件；内部自带阈值/异常兜底。
            if self.cfg.agent.skills_auto_learn and tool_total >= self.cfg.agent.skills_auto_learn_min_tools:
                asyncio.create_task(maybe_learn(
                    self.cfg, self.llm,
                    user_message=user_message, answer=answer,
                    traj_tools=traj_tools, steps_used=step + 1,
                    tool_total=tool_total, owner=owner,
                ))
            # 后台记忆反思：每轮成功回答后（opt-in），fork 廉价 LLM 调用把可沉淀的用户事实
            # 写入长期记忆 MEMORY.md。同样用 create_task 触发，异常安全，不写脏记忆。
            if self.cfg.agent.reflection_enabled:
                asyncio.create_task(maybe_reflect(
                    self.cfg, self.llm,
                    user_message=user_message, answer=answer, owner=owner,
                ))
            yield {"type": "done"}
            return

        # 达最大步数退出：若已执行工具，给出轨迹摘要而非静默 done（前端通常不展示 note）
        if tool_total > 0:
            bits = [f"- {t['name']}: {t['result'][:160]}" for t in traj_tools[-3:]]
            summary = (
                f"已达到最大推理步数（{max_steps}），本次已执行 {tool_total} 个工具步骤：\n"
                + "\n".join(bits)
                + "\n\n如需继续，可补充指令让任务延续。"
            )
            yield {"type": "token", "text": summary}
            if self.memory:
                self.memory.append(session, "assistant", summary)
        yield {"type": "done", "note": f"已达到最大推理步数({max_steps})"}

    # =====================================================================
    # 多模态附件组装（对标 Hermes 图片 / PDF 处理，零 OCR）
    # =====================================================================
    def _build_multimodal_content(self, user_text: str, attachments: list, model_name: str):
        """把本轮流附件组装为发给模型的 content（str 或 content 列表）。

        - 图片：image_input_mode=native 时转 base64 vision part + [Image attached at:] 提示；
                否则仅提示"模型无视觉"。
        - 扫描件 PDF（无文本层）：渲染每页为 PNG 视觉图片（不 OCR）；
        - 文本 PDF / 其他二进制文档：注入上下文提示，让 Agent 用 read_file 自取文本。
        处理失败的附件进入 skipped，一并告知模型，绝不静默丢弃。
        """
        cfg = self.cfg
        data_dir = cfg.server.data_dir
        store_dir = cfg.media.store_dir
        image_mode = image_routing.decide_image_input_mode(cfg, model_name)

        texts: list[str] = []
        if user_text:
            texts.append(user_text)

        image_parts: list[dict] = []
        notes: list[str] = []
        skipped: list[str] = []

        # ---- 图片 ----
        img_paths: list[str] = []
        for att in attachments:
            if not att.get("is_image"):
                continue
            if image_mode == "native":
                fpath = image_routing.resolve_attachment_file(att, data_dir, store_dir)
                if fpath:
                    img_paths.append(fpath)
                else:
                    skipped.append(f"{att.get('title', '图片')}: 找不到本地文件")
            else:
                notes.append(
                    f"[Note] 图片「{att.get('title', '')}」已上传，但当前模型未启用视觉能力"
                    f"（image_input_mode=text），无法分析其内容。")
        if img_paths:
            parts, sk = image_routing.build_native_content_parts(img_paths)
            image_parts.extend(parts)
            skipped.extend(f"{os.path.basename(p)}: {r}" for p, r in sk)
            if parts:
                hint = image_routing.build_image_attach_hint(img_paths)
                if hint:
                    notes.append(hint)

        # ---- PDF / 其他二进制文档 ----
        for att in attachments:
            ftype = (att.get("file_type") or "").upper()
            if att.get("is_image"):
                continue
            fpath = image_routing.resolve_attachment_file(att, data_dir, store_dir)
            if not fpath:
                skipped.append(f"{att.get('title', '文件')}: 找不到本地文件")
                continue
            if ftype == "PDF":
                if image_mode == "native" and not image_routing.pdf_has_text_layer(fpath):
                    pages, meta = image_routing.pdf_pages_to_data_urls(fpath)
                    if pages:
                        image_parts.extend(
                            {"type": "image_url", "image_url": {"url": u}} for u in pages)
                        notes.append(
                            f"[PDF attached: {att.get('title')} ({meta['pages']} page(s) "
                            f"rendered as images for vision, {meta['skipped']} skipped)] "
                            f"saved at: {fpath}")
                    else:
                        notes.append(image_routing.build_document_context_note(
                            att.get("title", "document"), fpath))
                else:
                    notes.append(image_routing.build_document_context_note(
                        att.get("title", "document"), fpath))
            else:
                notes.append(image_routing.build_document_context_note(
                    att.get("title", "document"), fpath))

        # ---- 组装最终 content ----
        if image_parts:
            content: list[dict] = []
            if texts or notes:
                content.append({"type": "text", "text": "\n".join(texts + notes)})
            content.extend(image_parts)
            if skipped:
                content.append({"type": "text",
                                 "text": "[Skipped attachments: " + "; ".join(skipped) + "]"})
            return content
        all_text = texts + notes
        if skipped:
            all_text.append("[Skipped attachments: " + "; ".join(skipped) + "]")
        return "\n".join(all_text) if all_text else (user_text or "")

    # =====================================================================
    # 长期记忆同步（向量 provider，opt-in；memory_manager 为 None 时全 no-op）
    # =====================================================================
    async def _sync_memory(self, owner: Optional[str], role: str, content: str) -> None:
        if self.memory_manager is None:
            return
        try:
            # MemoryManager.sync_turn 内部仅在存在外部 provider 时落库，否则直接返回
            await self.memory_manager.sync_turn(owner, role, content)
        except Exception:
            pass

    # =====================================================================
    # 多 Agent 委派：主管调用 delegate_to_agent 时，实时运行子智能体并转发事件
    # =====================================================================
    async def _run_delegate(self, args: dict, session: str, owner: Optional[str],
                            is_admin: bool = False) -> AsyncIterator[dict]:
        from ...context import get_ctx

        name = args.get("agent_name") or args.get("agent")
        task = (args.get("task") or "").strip()
        yield {"type": "delegate_start", "agent": name, "task": task}

        meta = get_agent_meta(name)
        if not meta:
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 未找到子智能体：{name}"}
            return
        # 多用户隔离：他人私有子智能体对当前用户不可见 → 视同不存在（防枚举探测）。
        if not can_view(meta.get("owner") or None, owner, is_admin, bool(meta.get("shared")),
                        meta.get("share_with") or None, user_role):
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 未找到子智能体：{name}"}
            return
        if not is_enabled(name):
            yield {"type": "delegate_end", "agent": name,
                   "result": f"[委派失败] 子智能体已停用：{name}"}
            return

        g = get_ctx()
        sub_ctx = ToolContext(
            kb=g.kb, security=g.cfg.security,
            user=owner or "anonymous", session=session, is_admin=is_admin,
        )
        sub = Agent(g.cfg, g.llm, g.kb, g.memory, sub_ctx, media=g.media,
                    context_engine=self.context_engine,
                    memory_manager=g.memory_manager)
        collected = []
        async for ev in sub.run(task, session, model=meta.get("model"),
                                owner=owner, agent_name=name, is_admin=is_admin,
                                user_role=user_role):
            et = ev.get("type")
            if et == "token":
                collected.append(ev["text"])
                yield {"type": "token", "text": ev["text"], "agent": name}
            elif et in ("tool_call", "tool_result"):
                yield {**ev, "agent": name}
        yield {"type": "delegate_end", "agent": name, "result": "".join(collected)}

    # =====================================================================
    # 图像生成分支
    # =====================================================================
    async def _run_image(self, prompt, session, pc, model, image=None) -> AsyncIterator[dict]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield {"type": "error", "message": "请输入图像描述（prompt）。"}
            yield {"type": "done"}
            return
        if self.memory:
            self.memory.append(session, "user", prompt)
        mode = "图生图" if image else "文生图"
        yield {"type": "status", "text": f"正在用 {model} {mode}…"}
        try:
            res = await self.llm.generate_image(pc, model, prompt, image=image)
            url = await self._persist_media(res, kind="img", default_ext="png")
        except Exception as e:
            msg = f"图像生成失败：{self._fmt_err(e)}"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[图像生成失败] {msg}")
            yield {"type": "done"}
            return
        yield {"type": "image", "url": url, "prompt": prompt, "model": model}
        if self.memory:
            self.memory.append(session, "assistant", f"[图像已生成] {prompt}\n{url}")
        yield {"type": "done"}

    # =====================================================================
    # 视频生成分支（异步：提交任务 + 轮询）
    # =====================================================================
    async def _run_video(self, prompt, session, pc, model, image=None) -> AsyncIterator[dict]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield {"type": "error", "message": "请输入视频描述（prompt）。"}
            yield {"type": "done"}
            return
        if self.memory:
            self.memory.append(session, "user", prompt)
        media = self.cfg.media
        mode = "图生视频" if image else "文生视频"
        yield {"type": "status", "text": f"正在用 {model} 提交{mode}任务…"}
        try:
            task_id = await self.llm.create_video_task(pc, model, prompt, image=image)
        except Exception as e:
            msg = f"视频任务提交失败：{self._fmt_err(e)}"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[视频生成失败] {msg}")
            yield {"type": "done"}
            return

        deadline = time.time() + media.video_timeout
        video_url = None
        last_progress = -1
        while time.time() < deadline:
            await asyncio.sleep(media.video_poll_interval)
            try:
                st = await self.llm.poll_video_once(pc, task_id)
            except Exception as e:
                yield {"type": "status", "text": f"轮询中（网络波动，重试）… {self._fmt_err(e)}"}
                continue
            status = st.get("status")
            prog = st.get("progress")
            if isinstance(prog, (int, float)) and prog != last_progress:
                last_progress = prog
                yield {"type": "status", "text": f"视频生成中… {int(prog)}%"}
            elif status:
                yield {"type": "status", "text": f"视频生成中…（{status}）"}
            if status in ("completed", "succeeded", "success"):
                video_url = st.get("video_url")
                break
            if status in ("failed", "error", "cancelled"):
                msg = f"视频生成失败：{st.get('error') or status}"
                yield {"type": "error", "message": msg}
                if self.memory:
                    self.memory.append(session, "assistant", f"[视频生成失败] {msg}")
                yield {"type": "done"}
                return

        if not video_url:
            msg = "视频生成超时，请稍后在提供商侧查询任务，或重试。"
            yield {"type": "error", "message": msg}
            if self.memory:
                self.memory.append(session, "assistant", f"[视频生成超时] {msg}")
            yield {"type": "done"}
            return

        yield {"type": "status", "text": "正在下载并保存视频…"}
        try:
            url = await self._persist_media({"url": video_url}, kind="vid", default_ext="mp4")
        except Exception:
            url = video_url
        yield {"type": "video", "url": url, "prompt": prompt, "model": model}
        if self.memory:
            self.memory.append(session, "assistant", f"[视频已生成] {prompt}\n{url}")
        yield {"type": "done"}

    # =====================================================================
    # 工具方法
    # =====================================================================
    @staticmethod
    def _first_model_of_kind(pc, kind: str) -> Optional[str]:
        for m in (pc.models or []):
            if classify_model(m) == kind:
                return m
        return None

    def _pick_img2img_model(self, pc, model: str) -> str:
        low = (model or "").lower()
        if "2.1" not in low:
            return model
        for m in (pc.models or []):
            ml = m.lower()
            if classify_model(m) == "image" and ("2.0" in ml or "edit" in ml):
                return m
        return model

    async def _persist_media(self, res: dict, kind: str, default_ext: str) -> str:
        if res.get("b64"):
            import base64

            data = base64.b64decode(res["b64"])
            if self.media:
                return self.media.save_bytes(data, kind=kind, ext=default_ext, owner=self.ctx.user)
            return "data:image/png;base64," + res["b64"]
        url = res.get("url")
        if not url:
            raise RuntimeError("生成结果为空")
        if not self.media:
            return url
        data = await self.llm.download(url)
        ext = type(self.media).guess_ext(url, fallback=default_ext)
        return self.media.save_bytes(data, kind=kind, ext=ext, owner=self.ctx.user)

    @staticmethod
    def _fmt_err(e: Exception) -> str:
        import httpx

        if isinstance(e, httpx.HTTPStatusError):
            body = ""
            try:
                body = e.response.text[:200]
            except Exception:
                pass
            return f"HTTP {e.response.status_code} {body}".strip()
        return str(e)[:200] or e.__class__.__name__
