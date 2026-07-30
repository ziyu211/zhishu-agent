"""智枢智能体 —— 模型 / Provider 管理路由。

照 hermes-web-ui ModelsView 的能力：Provider 列表（卡片）、添加（预设/自定义）、
更新（api_key/启停/优先级）、删除、设置默认模型；并提供预设与在线拉取模型。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from ..core.config import ZhishuConfig
from ..context import get_ctx

router = APIRouter(prefix="/api/v1", tags=["models"])


def _is_admin(user: dict) -> bool:
    return (user.get("r") or "") == "admin"


def _username(user: dict) -> str:
    return (user.get("u") or "").strip()


class AddProviderReq(BaseModel):
    provider_key: str | None = None   # 预设 key（如 qwen），自定义则为 None
    name: str | None = None
    label: str | None = None
    base_url: str
    api_key: str = ""
    model: str = ""                    # 默认模型
    models: list[str] | None = None
    local: bool = False
    priority: int = 50
    context_length: int | None = None
    shared: bool = False              # 显式共享：对他人可见可用（共享后他人可用其密钥，密钥对其脱敏）


class UpdateProviderReq(BaseModel):
    api_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    base_url: str | None = None
    models: list[str] | None = None
    shared: bool | None = None


class DefaultModelReq(BaseModel):
    model: str


class FetchModelsReq(BaseModel):
    base_url: str
    api_key: str = ""


@router.get("/models")
async def list_models(user=require_auth("models:read")):
    """对话页/选择器用：仅当前用户可见（本人 + 共享）的 Provider 及生效默认模型。"""
    ctx = get_ctx()
    uname, admin = _username(user), _is_admin(user)
    cfg = ctx.cfg.for_user(uname, admin)
    providers = []
    for pc in cfg.ordered_providers():
        providers.append({
            "provider": pc.name,
            "label": pc.label,
            "models": pc.models,
            "local": pc.local,
            "base_url": pc.base_url,
        })
    return {"default_model": cfg.default_model, "providers": providers}


@router.get("/models/presets")
async def presets(user=require_auth("models:read")):
    """内置国产/本地 Provider 预设，供添加表单选择。"""
    return {"presets": ZhishuConfig.presets()}


@router.get("/providers")
async def list_providers(user=require_auth("models:read")):
    """管理页用：当前用户可见的 Provider（含禁用），api_key 脱敏，附 owner/shared。"""
    ctx = get_ctx()
    uname, admin = _username(user), _is_admin(user)
    return {
        "default_model": ctx.providers.effective_default(None if admin else uname),
        "providers": ctx.providers.list(uname, admin),
    }


@router.post("/providers")
async def add_provider(req: AddProviderReq, user=require_auth("models:write")):
    ctx = get_ctx()
    # 预设：从内置补全 label/models/local/name
    name = req.name
    label = req.label
    models = list(req.models or [])
    local = req.local
    if req.provider_key:
        preset = next((p for p in ZhishuConfig.presets()
                       if p["provider"] == req.provider_key), None)
        if preset:
            name = name or preset["provider"]
            label = label or preset["label"]
            local = preset["local"]
            if not models:
                models = list(preset["models"])
    if not name:
        # 自定义：从 base_url 推断名称
        host = req.base_url.replace("https://", "").replace("http://", "").split("/")[0]
        name = host.replace(".", "_") or "custom"
        label = label or host
    if req.model and req.model not in models:
        models = [req.model] + models
    try:
        # 默认私有：归属创建者（含 admin 自身）；shared=True 才对他人可见可用。
        r = ctx.providers.add(
            name=name, label=label or name, base_url=req.base_url,
            api_key=req.api_key, models=models, local=local,
            priority=req.priority, context_length=req.context_length,
            owner=_username(user), shared=req.shared,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("real_u", user.get("u", "")), "add_provider", f"添加 Provider {name}")
    return r


@router.put("/providers/{name}")
async def update_provider(name: str, req: UpdateProviderReq, user=require_auth("models:write")):
    ctx = get_ctx()
    try:
        r = ctx.providers.update(
            name, api_key=req.api_key, enabled=req.enabled,
            priority=req.priority, base_url=req.base_url, models=req.models,
            shared=req.shared, username=_username(user), is_admin=_is_admin(user),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("real_u", user.get("u", "")), "update_provider", f"更新 Provider {name}")
    return r


@router.delete("/providers/{name}")
async def delete_provider(name: str, user=require_auth("models:write")):
    ctx = get_ctx()
    try:
        r = ctx.providers.remove(name, username=_username(user), is_admin=_is_admin(user))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("real_u", user.get("u", "")), "delete_provider", f"删除 Provider {name}")
    return r


@router.post("/models/default")
async def set_default(req: DefaultModelReq, user=require_auth("models:write")):
    ctx = get_ctx()
    # 真实 admin（非代管）设置全局默认（作为未单独配置的用户的兜底）；
    # 代管/普通用户设置本人默认模型。
    uname = None if _is_admin(user) else _username(user)
    try:
        r = ctx.providers.set_default(req.model, username=uname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.audit.log(user.get("real_u", user.get("u", "")), "set_default_model", f"默认模型 -> {req.model}")
    return r


@router.post("/models/fetch")
async def fetch_models(req: FetchModelsReq, user=require_auth("models:write")):
    """在线拉取某端点的可用模型（OpenAI 兼容 /models）。内网离线时会失败，属正常。"""
    base = req.base_url.rstrip("/")
    # 对齐 hermes-web-ui：base 以 /v数字 结尾则取 {base}/models，否则 {base}/v1/models
    import re
    url = base + "/models" if re.search(r"/v\d+/*$", base) else base + "/v1/models"
    headers = {}
    if req.api_key.strip():
        headers["Authorization"] = f"Bearer {req.api_key.strip()}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        ids = [m["id"] for m in data.get("data", []) if "id" in m]
        return {"models": ids}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"拉取失败：{e}")
