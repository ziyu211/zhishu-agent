"""智枢智能体 —— 国产 LLM Provider 客户端（统一 chat/stream/embed + 回退链）。

  本文件对应 Hermes 的「LLM 客户端 + 回退链」职责，但把**协议格式转换**下沉到
  `ProviderTransport`（见 ./base.py、./registry.py），把**客户端构建**下沉到
  adapters（见 ./adapters.py）。本类只负责 HTTP 调用、流式解析、重试与多模态。

  当以 api_mode="moa" 构建时（即配置了 moa Provider），chat/stream 会路由到
  MoAClient（并行多个 reference agent + 聚合），实现「多智能体伪装成一个 LLM client」。
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, AsyncIterator, Optional

import httpx

from ..config import ZhishuConfig, ProviderConfig
from .registry import get_transport


# OpenAI 兼容消息 / 工具结构（仅类型约定，运行时用 dict）
Message = dict
ToolSpec = dict


class _RetryableStatus(Exception):
    """内部异常：命中可重试 HTTP 状态码时抛出，携带响应对象。"""

    def __init__(self, resp: httpx.Response):
        self.resp = resp


def _extract_upstream_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                return (err.get("message") or str(err))[:300]
            if isinstance(err, str):
                return err[:300]
            if "message" in body:
                return str(body["message"])[:300]
    except Exception:
        pass
    try:
        txt = resp.text.strip()
        if txt:
            return txt[:300]
    except Exception:
        pass
    return ""


# 对话级瞬时故障重试：多智能体编排会在数秒内密集打同一个 Provider，很容易撞上
# 限流（429）。此前 429 会直接把该 Provider 判定为失败、遍历完回退链后抛
# 「所有 LLM Provider 均不可用」，表现为子智能体整体空输出。此处对**瞬时**故障
# （限流 / 网关抖动 / 超时）做指数退避重试，鉴权失败等永久性错误则立即失败不重试。
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504, 529}
_LLM_MAX_RETRIES = 5
_LLM_RETRY_BASE = 1.5
_LLM_RETRY_MAX = 15.0


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """尊重上游 Retry-After 响应头（秒 / HTTP-date 两种格式只解析前者）。"""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        v = resp.headers.get("Retry-After")
        return max(0.0, min(float(v), _LLM_RETRY_MAX)) if v else None
    except (TypeError, ValueError):
        return None


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                        httpx.RemoteProtocolError, httpx.PoolTimeout)):
        return True
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code in _TRANSIENT_STATUS


class LLMClient:
    """统一 LLM 客户端：封装 chat / stream / embed，并内置回退链。"""

    def __init__(self, cfg: ZhishuConfig, api_mode: str = "openai"):
        self.cfg = cfg
        self.api_mode = api_mode
        # 连接超时设短：本地推理端点（Ollama / vLLM）未启动时，防火墙常静默丢弃 SYN，
        # 默认 120s 总超时会让「无可用 LLM」的失败反馈卡很久；5s 连接超时即可快速判定。
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=10.0)
        )

    async def aclose(self):
        await self._http.aclose()

    # --------------------------- 非流式对话 ---------------------------
    async def chat(
        self,
        messages: list[Message],
        model: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        prefer: Optional[str] = None,
        tool_choice: Any = "auto",
    ) -> dict:
        if self.api_mode == "moa":
            from ..agent.moa import MoAClient

            return await MoAClient(self.cfg).chat(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )
        chain = self._build_chain(model)
        last_err: Optional[Exception] = None
        for pc, mdl in chain:
            for attempt in range(_LLM_MAX_RETRIES + 1):
                try:
                    return await self._chat_once(pc, mdl, messages, tools,
                                                 temperature, max_tokens, tool_choice)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt >= _LLM_MAX_RETRIES or not _is_transient(e):
                        break  # 永久性错误（401/400/模型不存在）→ 立刻切下一个 Provider
                    delay = _retry_after_seconds(e)
                    if delay is None:
                        # 指数退避 + 抖动，避免多个子智能体同步重试再次撞满限流窗口
                        delay = min(_LLM_RETRY_BASE * (2 ** attempt), _LLM_RETRY_MAX)
                        delay *= (0.7 + random.random() * 0.6)
                    await asyncio.sleep(delay)
        if getattr(getattr(last_err, "response", None), "status_code", None) == 429:
            raise RuntimeError(
                f"模型服务持续限流（HTTP 429），已重试 {_LLM_MAX_RETRIES} 次仍失败。"
                "多智能体编排会在短时间内密集调用模型，请降低并发/减少子智能体数量，"
                "或在「模型管理」中再配置一个 Provider 作为回退。")
        raise RuntimeError(f"所有 LLM Provider 均不可用：{last_err}")

    @staticmethod
    def _is_local(base_url: str) -> bool:
        """判断是否为本地/内网推理端点（Ollama / vLLM 等，无需 API Key）。"""
        u = (base_url or "").lower()
        return any(seg in u for seg in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))

    def _build_chain(self, prefer: Optional[str]):
        ordered = self.cfg.ordered_providers()
        # 候选链：先放显式指定的 prefer / default_model，再放其余有序 Provider。
        # 关键修复：prefer / default_model 也走与「有序列表」相同的「无 Key 且非本地则跳过」逻辑，
        # 否则缺 Key 的默认模型会被强制注入链首，发出无 Authorization 头的请求，
        # 被网关以 401 拒绝，从而把「缺 Key」这一根因掩盖成「所有 Provider 均不可用」。
        candidates: list[tuple] = []
        if prefer:
            pc, mdl = self.cfg.resolve_model(prefer)
            candidates.append((pc, mdl))
            ordered = [p for p in ordered if p.name != pc.name]
        if model_override := (self.cfg.default_model if not prefer else None):
            pc, mdl = self.cfg.resolve_model(model_override)
            if (pc, mdl) not in candidates:
                candidates.append((pc, mdl))
        for pc in ordered:
            candidates.append((pc, pc.models[0] if pc.models else "local-model"))

        chain = []
        missing_keys: list[tuple] = []  # (provider name, base_url) 缺 Key 的云端 Provider
        for pc, mdl in candidates:
            # 跳过「无 API Key 且非本地」的 Provider：云端密钥缺失必败，
            # 逐个发起网络探测既无效又拖慢失败反馈（被代理拦截时尤为明显）。
            # 本地端点（Ollama / vLLM）即使无 Key 也应尝试（连不上会快速拒绝）。
            if not pc.api_key and not self._is_local(pc.base_url):
                if pc.name not in [n for n, _ in missing_keys]:
                    missing_keys.append((pc.name, pc.base_url))
                continue
            chain.append((pc, mdl))
        if not chain:
            if missing_keys:
                names = "、".join(f"{n}（{u}）" for n, u in missing_keys)
                raise RuntimeError(
                    f"所有已配置的 LLM Provider 均缺少 API Key 或不可达：{names}。"
                    "请在「模型管理」中为这些 Provider 填写有效的 API Key"
                    "（本地推理端点 Ollama/vLLM 可留空 Key 但需开启并可达）。"
                )
            # 完全跟随配置：没有任何已配置可用的模型时给出明确指引，而非静默尝试预设端点。
            raise RuntimeError(
                "未配置任何可用的 LLM 模型。请在「模型管理」中添加 Provider"
                "（填写 API Key，或启用本地端点如 Ollama/vLLM）并设置默认模型。"
            )
        return chain

    async def _chat_once(self, pc, model, messages, tools, temperature, max_tokens,
                         tool_choice: Any = "auto") -> dict:
        transport = get_transport(self.api_mode)
        kw = transport.build_kwargs(
            messages, tools, temperature=temperature, max_tokens=max_tokens,
            stream=False, model=model, tool_choice=tool_choice,
        )
        url = pc.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._http.post(url, json=kw, headers=headers)
        resp.raise_for_status()
        out = transport.normalize_response(resp.json())
        # 若上游网关 / 代理返回「200 + 错误 JSON」（而非 4xx/5xx），
        # normalize_response 后依旧不含 choices —— 视为调用失败并抛出，
        # 以触发回退链，最终在没有可用 Provider 时统一抛 RuntimeError。
        # 否则错误体会被当成功结果原样返回，导致上层 resp["choices"] 直接 KeyError。
        if not isinstance(out, dict) or "choices" not in out:
            raise RuntimeError(f"Provider「{pc.name}」未返回有效补全：{str(out)[:200]}")
        return out

    # --------------------------- 流式对话 ---------------------------
    async def stream(self, messages, model=None, tools=None,
                     temperature=0.7, max_tokens=2048, prefer=None) -> AsyncIterator[str]:
        if self.api_mode == "moa":
            from ..agent.moa import MoAClient

            async for piece in MoAClient(self.cfg).stream(
                messages, model=model, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            ):
                yield piece
            return
        chain = self._build_chain(model)
        last_err = None
        for pc, mdl in chain:
            try:
                async for piece in self._stream_once(pc, mdl, messages, tools, temperature, max_tokens):
                    yield piece
                return
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"所有 LLM Provider 均不可用：{last_err}")

    async def _stream_once(self, pc, model, messages, tools, temperature, max_tokens):
        transport = get_transport(self.api_mode)
        kw = transport.build_kwargs(
            messages, tools, temperature=temperature, max_tokens=max_tokens,
            stream=True, model=model,
        )
        url = pc.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        async with self._http.stream("POST", url, json=kw, headers=headers) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    yield delta["content"]
                if "tool_calls" in delta:
                    yield f"\u0000TOOLCALL\u0000{json.dumps(delta['tool_calls'])}\u0000"

    # --------------------------- 上游重试 ---------------------------
    async def _request_with_retry(self, method, url, *, headers=None, json=None,
                                  follow_redirects=False) -> httpx.Response:
        media = self.cfg.media
        attempt = 0
        last_resp = None
        last_err = None
        while True:
            try:
                resp = await self._http.request(
                    method, url, headers=headers or {}, json=json,
                    follow_redirects=follow_redirects,
                )
            except httpx.TransportError as e:
                last_resp = None
                last_err = e
            else:
                last_resp = resp
                last_err = None
                if resp.status_code in media.retry_codes:
                    last_err = _RetryableStatus(resp)
                else:
                    return resp
            if attempt >= media.max_retries:
                break
            delay = min(media.retry_base_delay * (2 ** attempt), media.retry_max_delay)
            delay *= (0.5 + random.random())
            await asyncio.sleep(delay)
            attempt += 1
        if last_resp is not None:
            detail = _extract_upstream_detail(last_resp)
            raise RuntimeError(
                f"上游服务繁忙（HTTP {last_resp.status_code}），已重试 {media.max_retries} 次仍失败。"
                + (f" 原因：{detail}" if detail else "")
            )
        raise RuntimeError(
            f"调用上游服务失败（网络错误），已重试 {media.max_retries} 次：{last_err}"
        )

    # --------------------------- 图像生成 ---------------------------
    async def generate_image(self, pc, model, prompt, size=None, image=None) -> dict:
        media = self.cfg.media
        url = pc.base_url.rstrip("/") + media.image_path
        payload: dict = {"model": model, "prompt": prompt, "size": size or media.image_size}
        if image:
            imgs = image if isinstance(image, list) else [image]
            payload["image"] = [i for i in imgs if i]
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("POST", url, headers=headers, json=payload)
        data = resp.json()
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"图像接口未返回结果：{str(data)[:200]}")
        first = items[0]
        if first.get("url"):
            return {"url": first["url"]}
        if first.get("b64_json"):
            return {"b64": first["b64_json"]}
        raise RuntimeError("图像接口返回中既无 url 也无 b64_json")

    # --------------------------- 视频生成 ---------------------------
    async def create_video_task(self, pc, model, prompt, size=None, image=None) -> str:
        media = self.cfg.media
        url = pc.base_url.rstrip("/") + media.video_path
        w, h = self._parse_size(size or media.video_size)
        payload = {
            "model": model, "prompt": prompt, "width": w, "height": h,
            "num_frames": media.video_num_frames, "frame_rate": media.video_frame_rate,
        }
        if image:
            payload["image"] = image
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("POST", url, headers=headers, json=payload)
        data = resp.json()
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"视频任务未返回 task_id：{str(data)[:200]}")
        return str(task_id)

    async def poll_video_once(self, pc, task_id) -> dict:
        media = self.cfg.media
        path = media.video_poll_path.format(task_id=task_id)
        url = pc.base_url.rstrip("/") + path
        headers = {}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        resp = await self._request_with_retry("GET", url, headers=headers)
        data = resp.json()
        status = (data.get("status") or "").lower()
        # 优先取顶层 video_url / url；仅当二者皆空且 data 为列表时，再回退到列表首项。
        # 注意：此前整段表达式被三元运算符 `if isinstance(data.get("data"), list)` 包裹，
        # 导致「无 data 键但顶层已有 video_url」时被错误地置为 None。此处显式分离。
        video_url = data.get("video_url") or data.get("url")
        if not video_url:
            data_list = data.get("data")
            if isinstance(data_list, list) and data_list:
                first = data_list[0] if isinstance(data_list[0], dict) else {}
                video_url = first.get("url")
        return {"status": status, "progress": data.get("progress"),
                "video_url": video_url, "error": data.get("error"), "raw": data}

    # --------------------------- 下载产物 ---------------------------
    async def download(self, url: str) -> bytes:
        resp = await self._request_with_retry("GET", url, follow_redirects=True)
        return resp.content

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        try:
            w, h = size.lower().split("x", 1)
            return int(w), int(h)
        except Exception:
            return 1152, 768

    # --------------------------- Embedding ---------------------------
    async def embed(self, texts, pc: Optional[ProviderConfig] = None) -> list[list[float]]:
        pc = pc or self.cfg.get_provider("qwen") or next(iter(self.cfg.providers.values()), None)
        if not pc:
            raise RuntimeError("无可用 embedding provider")
        url = pc.base_url.rstrip("/") + "/embeddings"
        headers = {"Content-Type": "application/json"}
        if pc.api_key:
            headers[pc.auth_header] = f"{pc.auth_prefix} {pc.api_key}".strip()
        out = []
        for t in texts:
            resp = await self._http.post(url, json={"model": pc.models[0], "input": t}, headers=headers)
            resp.raise_for_status()
            out.append(resp.json()["data"][0]["embedding"])
        return out
