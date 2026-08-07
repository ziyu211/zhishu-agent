"""智枢智能体 —— Provider 凭证层（对标 Hermes `agent/credential_pool.py`）。

  职责：
    * ProviderStore  —— 持久层：把用户在界面上对 Provider 的增删改（含默认模型）
                          持久化到 data/providers.json，并直接作用于内存中的
                          cfg.providers，使 LLMClient 立即生效（无需重启）。
                          api_key 落盘前用 Crypto(SM4/XOR) 混淆。

  注：LLMClient 的回退链直接消费 cfg.ordered_providers()（按优先级遍历，单轮内失败即
  跳到下一 Provider），无需额外的运行期凭证池；故此处只保留持久层 ProviderStore。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Optional

from .config import ZhishuConfig, ProviderConfig, DEFAULT_PROVIDERS
from .security import Crypto
from .providers import compat as _compat


def _norm_compat(value) -> str:
    """把用户输入的 compat 值规整为标准 key（空 / 别名 / 大小写混写皆可）。"""
    key = (value or "").strip().lower().replace(" ", "")
    key = _compat.ALIASES.get(key, key)
    if not key:
        return ""   # 空 = 自动探测，交给运行期解析
    return key if key in _compat.PROFILES else ""


def _compat_profile(pc) -> "_compat.CompatProfile":
    return _compat.profile_for(pc)


def _forget_caps(base_url: str) -> None:
    """清空某端点已学到的运行期能力结论（切换框架 / 改地址后调用）。"""
    prefix = (base_url or "").rstrip("/")
    if not prefix:
        return
    try:
        for k in list(_compat.runtime_caps.snapshot().keys()):
            if k.split("|", 1)[0] == prefix:
                _compat.runtime_caps._d.pop(k, None)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 持久层：ProviderStore
# ---------------------------------------------------------------------------
class ProviderStore:
    def __init__(self, cfg: ZhishuConfig, path: str, crypto: Optional[Crypto] = None):
        self.cfg = cfg
        self.path = path
        self.crypto = crypto or Crypto(enable_sm=cfg.security.enable_sm)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # 原始密文缓存：轮换 security.secret 或卸载 gmssl 会导致解密失败（api_key 变空），
        # 此时 _save 必须保留磁盘上的原密文，绝不能把有效密钥静默覆盖成空串。
        self._raw: dict[str, str] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        providers: dict[str, ProviderConfig] = {}
        for d in data.get("providers", []):
            d = dict(d)
            key = d.get("api_key", "")
            if isinstance(key, str) and key.startswith(("sm4:", "xor:")):
                d["api_key"] = self._decrypt(key)
                self._raw[d["name"]] = key
            valid = {f for f in ProviderConfig.__dataclass_fields__}
            providers[d["name"]] = ProviderConfig(**{k: v for k, v in d.items() if k in valid})
        if providers:
            self.cfg.providers = providers
        if data.get("default_model"):
            self.cfg.default_model = data["default_model"]
        if isinstance(data.get("defaults"), dict):
            self.cfg.defaults = dict(data["defaults"])

    def _save(self):
        arr = []
        for p in self.cfg.providers.values():
            d = asdict(p)
            raw = self._raw.get(p.name)
            if d.get("api_key"):
                d["api_key"] = self.crypto.encrypt_sm4(self.cfg.security.secret, d["api_key"])
            elif raw:
                # 内存中明文为空（如密钥轮换后解密失败）：保留原密文，避免静默清空有效密钥。
                d["api_key"] = raw
            arr.append(d)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "providers": arr,
                "default_model": self.cfg.default_model,
                "defaults": self.cfg.defaults,
            }, f, ensure_ascii=False, indent=2)

    def _decrypt(self, blob: str) -> str:
        try:
            if blob.startswith("xor:"):
                raw = bytes.fromhex(blob[4:])
                k = self.crypto._derive_key(self.cfg.security.secret)
                return bytes(b ^ k[i % len(k)] for i, b in enumerate(raw)).decode("utf-8")
            if blob.startswith("sm4:") and self.crypto.use_sm:
                from gmssl import sm4

                cipher = sm4.CryptSM4()
                cipher.set_key(self.crypto._derive_key(self.cfg.security.secret), sm4.SM4_DECRYPT)
                return cipher.crypt_ecb(bytes.fromhex(blob[4:])).decode("utf-8")
        except Exception:
            return ""
        return ""

    def list(self, username: Optional[str] = None, is_admin: bool = False,
             user_role: Optional[str] = None) -> list[dict]:
        out = []
        for p in sorted(self.cfg.providers.values(), key=lambda x: x.priority):
            # 多用户隔离：非 admin 仅见「本人 + 共享 + 角色命中」Provider
            if not is_admin and not (
                (not p.owner)                      # 公共 Provider（owner 为空）：全员可见
                or p.owner == (username or "")
                or p.shared
                or (p.share_with and user_role in p.share_with)
            ):
                continue
            out.append({
                "provider": p.name, "label": p.label, "base_url": p.base_url,
                "models": p.models, "local": p.local, "enabled": p.enabled,
                "priority": p.priority, "has_key": bool(p.api_key),
                "api_key_masked": self._mask(p.api_key),
                "builtin": p.name in DEFAULT_PROVIDERS,
                "owner": p.owner or None,
                "shared": p.shared,
                "share_with": list(p.share_with or []),
                "context_length": p.context_length,
                # 推理框架兼容画像：compat 为用户显式选择（""=自动），
                # compat_effective 为本次实际生效的画像 key（自动探测结果），前端展示用。
                "compat": getattr(p, "compat", "") or "",
                "compat_effective": _compat_profile(p).key,
            })
        return out

    @staticmethod
    def _mask(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return "*" * len(key)
        return key[:4] + "****" + key[-4:]

    def add(self, *, name, label, base_url, api_key="", models=None, local=False,
            priority=50, context_length=None, compat="", owner="", shared=False,
            share_with=None) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Provider 名称不能为空")
        # 防越权覆盖：名称已存在且不属于当前创建者（他人或公共 Provider）时拒绝，
        # 避免普通用户拿到 models:write 后通过同名 add 覆盖管理员/他人的 Provider。
        existing = self.cfg.providers.get(name)
        if existing is not None and (existing.owner or "") != (owner or ""):
            raise ValueError("Provider 名称已存在，且属于其他用户或公共配置，无法覆盖（请使用其他名称）")
        try:
            _ctxlen = int(context_length) if context_length is not None else None
        except (TypeError, ValueError):
            _ctxlen = None
        pc = ProviderConfig(
            name=name, label=label or name, base_url=base_url.strip(),
            api_key=api_key.strip(), models=models or [], local=local,
            enabled=True, priority=priority,
            owner=owner or "", shared=bool(shared),
            share_with=list(share_with or []),
            context_length=_ctxlen if (_ctxlen and _ctxlen > 0) else None,
            compat=_norm_compat(compat),
        )
        self.cfg.providers[name] = pc
        self._save()
        return {"ok": True, "provider": name}

    def update(self, name, *, api_key=None, enabled=None, priority=None,
               base_url=None, models=None, shared=None, share_with=None,
               context_length=None, compat=None,
               username: Optional[str] = None, is_admin: bool = False) -> dict:
        pc = self.cfg.providers.get(name)
        if not pc:
            raise ValueError("Provider 不存在")
        # 多用户隔离：仅 owner 本人（或 admin）可改；共享/角色共享项非 owner 不可改
        if not is_admin and (pc.owner or "") != (username or ""):
            raise PermissionError("无权修改该 Provider（仅本人或管理员可管理；公共 Provider 仅管理员可改）")
        if api_key is not None and api_key != "":
            pc.api_key = api_key.strip()
        if enabled is not None:
            pc.enabled = enabled
        if priority is not None:
            pc.priority = priority
        if base_url is not None and base_url.strip():
            pc.base_url = base_url.strip()
        if models is not None:
            pc.models = models
        if shared is not None:
            pc.shared = bool(shared)
        if share_with is not None:
            pc.share_with = list(share_with or [])
        if context_length is not None:
            # 传 0 / 负数 / 非法值 → 视为「清空，恢复未知」，不写脏数据
            try:
                n = int(context_length)
            except (TypeError, ValueError):
                n = 0
            pc.context_length = n if n > 0 else None
        if compat is not None:
            # 切换推理框架后，此前学到的端点能力结论（如「不支持 tools」）失效，需清空重学
            pc.compat = _norm_compat(compat)
            _forget_caps(pc.base_url)
        if base_url is not None and base_url.strip():
            _forget_caps(pc.base_url)
        self._save()
        return {"ok": True, "provider": name}

    def remove(self, name, *, username: Optional[str] = None,
               is_admin: bool = False) -> dict:
        pc = self.cfg.providers.get(name)
        if not pc:
            raise ValueError("Provider 不存在")
        if not is_admin and (pc.owner or "") != (username or ""):
            raise PermissionError("无权删除该 Provider（仅本人或管理员可管理；公共 Provider 仅管理员可删）")
        del self.cfg.providers[name]
        self._save()
        return {"ok": True}

    def delete_by_owner(self, owner: str) -> int:
        """级联删除：删除某用户拥有的全部 Provider（用于删除用户时清理孤儿凭证）。"""
        removed = [n for n, p in self.cfg.providers.items() if (p.owner or "") == owner]
        for n in removed:
            del self.cfg.providers[n]
        if removed:
            self._save()
        return len(removed)

    def set_default(self, model: str, username: Optional[str] = None) -> dict:
        if not model:
            raise ValueError("默认模型不能为空")
        if username:
            self.cfg.defaults[username] = model
        else:
            self.cfg.default_model = model
        self._save()
        return {"ok": True, "default_model": model}

    def effective_default(self, username: Optional[str] = None) -> str:
        """返回该用户生效的默认模型（优先用户级，回退全局）。"""
        if username and username in self.cfg.defaults:
            return self.cfg.defaults[username]
        return self.cfg.default_model

