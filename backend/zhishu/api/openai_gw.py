"""OpenAI 兼容服务端网关（/v1/chat/completions + /v1/models）。

目标：让智枢可作为 OpenAI 兼容后端，对接 Open WebUI / LobeChat / 各种
兼容 OpenAI 协议的开源前端与 SDK，同时**复用现有 RBAC**（Bearer Token 鉴权、
按用户收窄可见 Provider/模型、并发限流）。

设计取舍：
- 走「直连 LLM 流式」（ctx.llm.stream / chat），给出标准 OpenAI 语义：
  客户端自管历史（messages 数组），服务端只透传模型 token。这是与生态对接的
  最小且最稳形态。智枢自身的 RAG / 工具 / 系统提示等 agent 能力由原生 Web UI
  提供；网关层保持纯粹，避免与客户端自带的 system prompt / 工具系统冲突。
- 鉴权：复用 require_auth("chat") / require_auth("models:read")，与对话页一致。
- 隔离：用 cfg.for_user(...) 把 LLMClient 收窄为「当前用户可见」的配置副本，
  普通用户无法越权调用他人私有 Provider。
- 工具调用：上游若返回 tool_calls，会以哨兵串混入流；网关将其翻译回 OpenAI
  的 delta.tool_calls 增量格式，使支持函数调用的客户端可正常工作。
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import require_auth
from ..context import get_ctx
from ..core.providers.client import LLMClient
from ..core.concurrency import get_limiter, ConcurrencyLimitError

router = APIRouter(prefix="/v1", tags=["openai-compat"])

# 上游 LLMClient.stream 用此哨兵包裹 tool_calls 增量，需还原为 OpenAI 格式
_TOOLCALL_SENTINEL = "\u0000TOOLCALL\u0000"


def _resolve_model_out(ctx, cfg_user, requested: str | None) -> str:
    """解析最终用于响应回显的模型名（失败则优雅降级）。"""
    try:
        _, mdl = cfg_user.resolve_model(requested)
        return mdl or requested or "unknown"
    except Exception:
        return requested or "unknown"


def _convert_messages(raw: list) -> list[dict]:
    """把 OpenAI 格式 messages 透传给上游（各 Provider 均为 OpenAI 兼容）。

    仅搬运上游能识别的字段，忽略未知字段，保证多模态 content 列表等原样传递。
    """
    out = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        mm: dict = {"role": m.get("role", "user")}
        if m.get("content") is not None:
            mm["content"] = m["content"]
        if m.get("name"):
            mm["name"] = m["name"]
        if m.get("tool_call_id"):
            mm["tool_call_id"] = m["tool_call_id"]
        if m.get("tool_calls"):
            mm["tool_calls"] = m["tool_calls"]
        out.append(mm)
    return out


@router.get("/models")
async def list_models(user=require_auth("models:read")):
    """OpenAI 兼容模型列表（仅当前用户可见 Provider 下的模型）。"""
    ctx = get_ctx()
    uname, is_admin = user.get("u", ""), user.get("r") == "admin"
    cfg = ctx.cfg.for_user(uname, is_admin, user.get("r", ""))
    data = []
    for pc in cfg.ordered_providers():
        for m in pc.models:
            mid = f"{pc.name}/{m}"
            data.append({
                "id": mid,
                "object": "model",
                "created": 0,
                "owned_by": pc.name,
                "permission": [],
                "root": mid,
                "parent": None,
            })
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request, user=require_auth("chat")):
    ctx = get_ctx()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "error": {"message": "请求体不是合法 JSON", "type": "invalid_request_error"}})

    raw_messages = body.get("messages") or []
    if not raw_messages:
        return JSONResponse(status_code=400, content={
            "error": {"message": "messages 不能为空", "type": "invalid_request_error"}})

    model = body.get("model")
    stream = bool(body.get("stream", False))
    try:
        temperature = float(body.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 2048
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        max_tokens = 2048
    tools = body.get("tools")
    tool_choice = body.get("tool_choice", "auto")

    owner = user.get("u", "")
    is_admin = user.get("r") == "admin"
    role = user.get("r", "")
    # RBAC 收窄：仅当前用户可见（本人 + 共享 + 角色命中）的 Provider/模型
    cfg_user = ctx.cfg.for_user(owner, is_admin, role)
    llm = LLMClient(cfg_user, ctx.llm.api_mode)

    # 回显：优先用客户端请求的 model id（如 agnes1/agnes-2.5-flash），
    # 未指定时再解析默认模型，符合 OpenAI 回显习惯。
    model_out = model or _resolve_model_out(ctx, cfg_user, model)
    messages = _convert_messages(raw_messages)

    cid = "chatcmpl-" + uuid.uuid4().hex
    created = int(time.time())

    # ---------- 非流式 ----------
    if not stream:
        limiter = get_limiter()
        try:
            await limiter.acquire(owner)
        except ConcurrencyLimitError as e:
            return JSONResponse(status_code=429, content={
                "error": {"message": str(e), "type": "rate_limit_error"}})
        try:
            try:
                out = await llm.chat(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                )
            except TypeError:
                # 个别 transport 的 chat 不接受 tool_choice 关键字时退化
                out = await llm.chat(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, tools=tools,
                )
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=500, content={
                "error": {"message": str(e), "type": "server_error"}})
        finally:
            await limiter.release(owner)
        # out 已是 OpenAI 原生字典（含 choices）；规整回显字段
        if isinstance(out, dict):
            out.setdefault("id", cid)
            out["created"] = created
            out["model"] = model_out
            out.setdefault("object", "chat.completion")
        ctx.audit.log(user.get("real_u", owner), "openai_chat",
                      str(raw_messages[-1].get("content", ""))[:200],
                      f"model={model_out}")
        return out

    # ---------- 流式（SSE，标准 OpenAI chunk 格式）----------
    async def event_gen():
        limiter = get_limiter()
        acquired = False
        try:
            await limiter.acquire(owner)
            acquired = True
        except ConcurrencyLimitError as e:
            err = {"error": {"message": str(e), "type": "rate_limit_error"}}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            # 首包：声明 assistant 角色
            first = {
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model_out,
                "choices": [{"index": 0, "delta": {"role": "assistant"},
                             "finish_reason": None}],
            }
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            try:
                stream_coro = llm.stream(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, tools=tools,
                )
                async for piece in stream_coro:
                    if isinstance(piece, str) and piece.startswith(_TOOLCALL_SENTINEL):
                        payload = piece[len(_TOOLCALL_SENTINEL):-1]  # 去掉首尾 \u0000
                        try:
                            tc = json.loads(payload)
                        except Exception:
                            continue
                        chunk = {
                            "id": cid, "object": "chat.completion.chunk",
                            "created": created, "model": model_out,
                            "choices": [{"index": 0, "delta": {"tool_calls": tc},
                                         "finish_reason": None}],
                        }
                    else:
                        text = piece if isinstance(piece, str) else str(piece)
                        chunk = {
                            "id": cid, "object": "chat.completion.chunk",
                            "created": created, "model": model_out,
                            "choices": [{"index": 0, "delta": {"content": text},
                                         "finish_reason": None}],
                        }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                err = {"error": {"message": str(e), "type": "server_error"}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            # 结束包
            end = {
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model_out,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
        finally:
            if acquired:
                await limiter.release(owner)
        yield "data: [DONE]\n\n"

    ctx.audit.log(user.get("real_u", owner), "openai_chat_stream",
                  str(raw_messages[-1].get("content", ""))[:200],
                  f"model={model_out}")
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )
