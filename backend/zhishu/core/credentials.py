"""智枢智能体 —— Provider 凭证层（对标 Hermes `agent/credential_pool.py`）。

  两层职责：
    * ProviderStore  —— 持久层：把用户在界面上对 Provider 的增删改（含默认模型）
                          持久化到 data/providers.json，并直接作用于内存中的
                          cfg.providers，使 LLMClient 立即生效（无需重启）。
                          api_key 落盘前用 Crypto(SM4/XOR) 混淆。
    * CredentialPool —— 运行期凭证池：在 ProviderStore 之上提供「多源优先级 +
                          临时不可用标记 + 刷新」能力。LLMClient 的回退链可直接消费
                          pool.candidates()，命中连续失败时把该 provider 临时移出本轮链。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Optional

from .config import ZhishuConfig, ProviderConfig, DEFAULT_PROVIDERS
from .security import Crypto


# ---------------------------------------------------------------------------
# 持久层：ProviderStore
# ---------------------------------------------------------------------------
class ProviderStore:
    def __init__(self, cfg: ZhishuConfig, path: str, crypto: Optional[Crypto] = None):
        self.cfg = cfg
        self.path = path
        self.crypto = crypto or Crypto(enable_sm=cfg.security.enable_sm)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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
            valid = {f for f in ProviderConfig.__dataclass_fields__}
            providers[d["name"]] = ProviderConfig(**{k: v for k, v in d.items() if k in valid})
        if providers:
            self.cfg.providers = providers
        if data.get("default_model"):
            self.cfg.default_model = data["default_model"]

    def _save(self):
        arr = []
        for p in self.cfg.providers.values():
            d = asdict(p)
            if d.get("api_key"):
                d["api_key"] = self.crypto.encrypt_sm4(self.cfg.security.secret, d["api_key"])
            arr.append(d)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"providers": arr, "default_model": self.cfg.default_model},
                      f, ensure_ascii=False, indent=2)

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

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.cfg.providers.values(), key=lambda x: x.priority):
            out.append({
                "provider": p.name, "label": p.label, "base_url": p.base_url,
                "models": p.models, "local": p.local, "enabled": p.enabled,
                "priority": p.priority, "has_key": bool(p.api_key),
                "api_key_masked": self._mask(p.api_key),
                "builtin": p.name in DEFAULT_PROVIDERS,
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
            priority=50, context_length=None) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Provider 名称不能为空")
        pc = ProviderConfig(
            name=name, label=label or name, base_url=base_url.strip(),
            api_key=api_key.strip(), models=models or [], local=local,
            enabled=True, priority=priority,
        )
        self.cfg.providers[name] = pc
        self._save()
        return {"ok": True, "provider": name}

    def update(self, name, *, api_key=None, enabled=None, priority=None,
               base_url=None, models=None) -> dict:
        pc = self.cfg.providers.get(name)
        if not pc:
            raise ValueError("Provider 不存在")
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
        self._save()
        return {"ok": True, "provider": name}

    def remove(self, name) -> dict:
        if name not in self.cfg.providers:
            raise ValueError("Provider 不存在")
        del self.cfg.providers[name]
        self._save()
        return {"ok": True}

    def set_default(self, model: str) -> dict:
        if not model:
            raise ValueError("默认模型不能为空")
        self.cfg.default_model = model
        self._save()
        return {"ok": True, "default_model": model}


# ---------------------------------------------------------------------------
# 运行期：CredentialPool（多源优先级 + 临时不可用 + 刷新）
# ---------------------------------------------------------------------------
class CredentialPool:
    """在 ProviderStore 之上提供运行期凭证池。

    * candidates()      —— 按优先级返回当前可用的 Provider 列表（供回退链消费）。
    * mark_unavailable(name) —— 把某 provider 临时移出本轮链（内存态，不落盘）。
    * refresh()         —— 从 providers.json 重新载入（密钥变更后无需重启）。
    * get(name)         —— 取指定 provider 配置。
    """

    def __init__(self, store: ProviderStore):
        self.store = store
        self._unavailable: set[str] = set()

    def candidates(self) -> list[ProviderConfig]:
        out = []
        for p in self.store.cfg.ordered_providers():
            if p.name in self._unavailable:
                continue
            out.append(p)
        return out

    def mark_unavailable(self, name: str) -> None:
        self._unavailable.add(name)

    def is_available(self, name: str) -> bool:
        return name not in self._unavailable

    def refresh(self) -> None:
        self._unavailable.clear()
        self.store._load()

    def get(self, name: str) -> Optional[ProviderConfig]:
        return self.store.cfg.providers.get(name)

    def default(self) -> Optional[ProviderConfig]:
        try:
            return self.store.cfg.resolve_model(self.store.cfg.default_model)[0]
        except Exception:
            return None
