"""智枢智能体 —— 安全合规模块（国产自主可控）。

包含：
  * 国密适配：SM3(哈希) / SM4(对称) / SM2(非对称)，优先 gmssl，缺失则降级到标准算法，
    对外接口保持一致（满足"接口国产化"要求，可平滑切换）。
  * 鉴权：基于 HMAC 的 Token（可选 SM3 签名），RBAC 角色。
  * 审计：所有敏感操作写入审计日志（SQLite）。
  * 出网控制：工具执行前检查是否允许访问外部网络（默认内网隔离）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Optional

from .config import SecurityConfig


# ----------------------------- 国密适配层 -----------------------------
class Crypto:
    """统一密码学接口；优先国密 gmssl，缺失降级标准库。"""

    def __init__(self, enable_sm: bool = True):
        self.use_sm = False
        self._sm = None
        if enable_sm:
            try:
                import gmssl  # 国产密码库（需 pip install gmssl，国内源可装）
                self._sm = gmssl
                self.use_sm = True
            except Exception:
                self.use_sm = False

    def hash(self, data: str) -> str:
        """SM3 优先，否则 SHA-256。"""
        if self.use_sm and hasattr(self._sm, "sm3"):
            try:
                return "sm3:" + self._sm.sm3.sm3_hash(data.encode("utf-8")).hex()
            except Exception:
                pass
        return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()

    def sign(self, secret: str, payload: str) -> str:
        """SM3-HMAC 优先，否则 HMAC-SHA256。"""
        if self.use_sm and hasattr(self._sm, "sm3"):
            try:
                from gmssl import sm3
                # SM3-HMAC 简化实现
                return hmac.new(secret.encode(), payload.encode(),
                                lambda d: bytes.fromhex(sm3.sm3_hash(d).hex())).hexdigest()
            except Exception:
                pass
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def encrypt_sm4(self, key: str, plaintext: str) -> str:
        """SM4 加密（密钥派生自 key）。缺失 gmssl 时降级 base64+异或混淆（仅防明文泄露）。"""
        if self.use_sm and hasattr(self._sm, "sm4"):
            try:
                from gmssl import sm4
                cipher = sm4.CryptSM4()
                cipher.set_key(self._derive_key(key), sm4.SM4_ENCRYPT)
                return "sm4:" + cipher.crypt_ecb(plaintext.encode("utf-8")).hex()
            except Exception:
                pass
        # 降级：异或混淆（非强加密，仅避免明文落盘）
        k = self._derive_key(key)
        out = bytes(b ^ k[i % len(k)] for i, b in enumerate(plaintext.encode("utf-8")))
        return "xor:" + out.hex()

    def _derive_key(self, key: str) -> bytes:
        return hashlib.sha256(key.encode()).digest()


# ----------------------------- 鉴权 / RBAC -----------------------------
# 角色权限矩阵（"*" 表示全部权限）。
# 权限命名规则：<模块>:<read|write>，写权限隐含读权限（见 AuthService.can）。
#   admin    系统管理员：全部权限（含用户/系统管理）
#   operator 运维/配置：可管理模型/知识库/技能插件MCP记忆/智能体/定时任务、查看审计，但不可管理用户与系统
#   user     普通用户：对话 + 知识库读写 + 管理自有（owner 隔离）的技能/插件/MCP/记忆/智能体
#                       + 配置「自己的专属模型」（新增/编辑/删除本人 Provider、设本人默认模型，ProviderStore 强制 owner 隔离）
#                       + 查看模型与全部可读模块 + 定时任务（仅本人任务，owner 隔离）。
#                       注意：普通用户对自己创建的模块/Provider 拥有写权限（与前端 markEditable/canEditItem 的
#                       owner 判定一致），但不可管理他人或公共 Provider，也不可管理用户/系统/公共模型配置。
#   viewer   只读访客：仅对话与模型查看
ROLES: dict[str, list[str]] = {
    "admin": [
        "*",
        # 以下为 admin 专属端点的显式声明（"*" 已隐含，列出仅为可读性）：
        "users:read", "users:write",
        "system:read", "admin", "settings:read", "settings:write",
    ],
    "operator": [
        "chat",
        "knowledge:read", "knowledge:write",
        "models:read", "models:write",
        "modules:read", "modules:write",
        "agents:read", "agents:write",
        "cron:read", "cron:write",
        "audit:read",
    ],
    "user": [
        "chat",
        "knowledge:read", "knowledge:write",
        # 普通用户可配置「自己的专属模型」：新增/编辑/删除本人归属的 Provider，并设置本人默认模型。
        # 后端 ProviderStore 强制 owner 隔离（仅本人/管理员可改删），且禁止覆盖他人或公共 Provider。
        "models:read", "models:write",
        # 普通用户管理自有模块（owner 隔离，后端 can_edit_meta/can_view_meta 强制校验）
        "modules:read", "modules:write",
        "agents:read", "agents:write",
        "cron:read", "cron:write",
    ],
    "viewer": [
        "chat",
        "models:read",
        "modules:read",
        "agents:read",
        "cron:read",
    ],
}

ROLE_LABELS = {
    "admin": "系统管理员",
    "operator": "运维/配置",
    "user": "普通用户",
    "viewer": "只读访客",
}


class UserStore:
    """多用户存储（SQLite）。密码以 Crypto.hash（SM3 优先）加盐存储。"""

    def __init__(self, crypto: Crypto, path: str = "data/zhishu_users.db"):
        self.crypto = crypto
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT DEFAULT '',
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_login DATETIME
            )"""
        )
        self.conn.commit()
        # 改密失效：password_epoch（改密后自增；令牌含 epoch 声明，校验不符即失效）。
        # 旧库无此列时安全加列，默认值 0 与历史令牌（无 e 声明）兼容，不会误杀会话。
        try:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN password_epoch INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.commit()
        except Exception:
            pass  # 列已存在

    # --------------------- 内部工具 ---------------------
    def _hash(self, password: str, salt: str) -> str:
        return self.crypto.hash(f"{salt}:{password}")

    def _row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d.pop("salt", None)
        d.pop("password_hash", None)
        d["role_label"] = ROLE_LABELS.get(d.get("role", ""), d.get("role", ""))
        return d

    # --------------------- 引导 ---------------------
    def bootstrap(self, admin_user: str, admin_password: str):
        """首次启动：若无任何用户，则用配置里的管理员账号建立首个 admin。"""
        cur = self.conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if cur["c"] == 0:
            self.create(admin_user, admin_password, role="admin",
                        display_name="系统管理员")

    # --------------------- 查询 ---------------------
    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    def list(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM users ORDER BY id ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_by_name(self, username: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

    def get(self, uid: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return self._row(row) if row else None

    # --------------------- 变更 ---------------------
    def create(self, username: str, password: str, role: str = "user",
               display_name: str = "") -> dict:
        username = (username or "").strip()
        if not username:
            raise ValueError("用户名不能为空")
        if role not in ROLES:
            raise ValueError(f"非法角色：{role}")
        if len(password or "") < 6:
            raise ValueError("密码长度至少 6 位")
        if self.get_by_name(username):
            raise ValueError("用户名已存在")
        salt = secrets.token_hex(8)
        pwd = self._hash(password, salt)
        cur = self.conn.execute(
            """INSERT INTO users (username, display_name, salt, password_hash, role, status)
               VALUES (?,?,?,?,?,'active')""",
            (username, display_name or username, salt, pwd, role),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def update(self, uid: int, *, role: Optional[str] = None,
               status: Optional[str] = None,
               display_name: Optional[str] = None) -> dict:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        new_role = role if role is not None else row["role"]
        new_status = status if status is not None else row["status"]
        new_name = display_name if display_name is not None else row["display_name"]
        if new_role not in ROLES:
            raise ValueError(f"非法角色：{new_role}")
        if new_status not in ("active", "disabled"):
            raise ValueError("非法状态")
        # 不允许停用/降级最后一个 admin
        if row["role"] == "admin" and (new_role != "admin" or new_status != "active"):
            admins = self.conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active'"
            ).fetchone()["c"]
            if admins <= 1:
                raise ValueError("必须保留至少一个启用的管理员账号")
        self.conn.execute(
            "UPDATE users SET role=?, status=?, display_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_role, new_status, new_name, uid),
        )
        self.conn.commit()
        return self.get(uid)

    def set_password(self, uid: int, password: str):
        if len(password or "") < 6:
            raise ValueError("密码长度至少 6 位")
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        salt = secrets.token_hex(8)
        pwd = self._hash(password, salt)
        self.conn.execute(
            "UPDATE users SET salt=?, password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (salt, pwd, uid),
        )
        self.conn.commit()

    def bump_epoch(self, uid: int) -> None:
        """改密后使该用户所有历史令牌失效：epoch 自增，verify 校验不符即拒绝。"""
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        self.conn.execute(
            "UPDATE users SET password_epoch = password_epoch + 1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (uid,),
        )
        self.conn.commit()

    def delete(self, uid: int):
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if row["role"] == "admin":
            admins = self.conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active'"
            ).fetchone()["c"]
            if admins <= 1:
                raise ValueError("必须保留至少一个启用的管理员账号")
        self.conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        self.conn.commit()

    def verify_password(self, username: str, password: str) -> Optional[dict]:
        row = self.get_by_name(username)
        if not row or row["status"] != "active":
            return None
        if not hmac.compare_digest(self._hash(password, row["salt"]), row["password_hash"]):
            return None
        self.conn.execute(
            "UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
        )
        self.conn.commit()
        return {"id": row["id"], "username": row["username"], "role": row["role"]}


class AuthService:
    def __init__(self, cfg: SecurityConfig, users: Optional["UserStore"] = None):
        self.cfg = cfg
        self.crypto = Crypto(cfg.enable_sm)
        self.secret = cfg.secret
        self.users = users

    def login(self, username: str, password: str) -> Optional[dict]:
        """返回 {token, user, role, display_name} 或 None。"""
        if not self.cfg.enable_auth:
            return self._session(username or "anonymous", "admin", username or "anonymous")
        # 1) 多用户库优先
        if self.users:
            u = self.users.verify_password(username, password)
            if u:
                full = self.users.get(u["id"]) or {}
                return self._session(u["username"], u["role"],
                                     full.get("display_name", u["username"]),
                                     epoch=full.get("password_epoch", 0))
        # 2) 引导管理员（用户库为空时可用配置账号登录）
        if (not self.users or self.users.count() == 0) and \
                username == self.cfg.admin_user and password == self.cfg.admin_password:
            return self._session(username, "admin", "系统管理员")
        return None

    def _session(self, user: str, role: str, display_name: str, epoch: int = 0) -> dict:
        return {
            "token": self._token(user, role, epoch=epoch),
            "user": user,
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "display_name": display_name,
            "perms": ROLES.get(role, []),
        }

    def _token(self, user: str, role: str, ttl: int = 86400 * 7, epoch: int = 0) -> str:
        payload = json.dumps({"u": user, "r": role, "e": epoch, "exp": int(time.time()) + ttl})
        sig = self.crypto.sign(self.secret, payload)
        return f"{payload}.{sig}"

    def verify(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        try:
            payload_b64, sig = token.rsplit(".", 1)
            if not hmac.compare_digest(self.crypto.sign(self.secret, payload_b64), sig):
                return None
            data = json.loads(payload_b64)
            if data.get("exp", 0) < time.time():
                return None
            # 吊销检查：用户被删除/停用后，旧令牌立即失效；角色被降级后，令牌里的
            # 旧角色不再被信任（避免「降级无效」）。
            # 注意三种「查不到用户」的语义必须区分，否则会误杀合法令牌：
            #   a) 无用户库 / 关闭鉴权(匿名令牌)   -> 放行（无从校验，且系统本就不做用户管理）
            #   b) 用户库为空（首次引导期）        -> 放行（bootstrap 尚未落库）
            #   c) 用户库非空但查无此人（已删除）  -> 拒绝（吊销生效）
            if self.users is not None and data.get("u"):
                row = None
                lookup_ok = True
                try:
                    row = self.users.get_by_name(data["u"])
                except Exception:
                    lookup_ok = False          # 存储异常：不因基础设施抖动误杀会话
                if lookup_ok:
                    if row is not None:
                        u = dict(row)
                        if (u.get("status") or "active") != "active":
                            return None        # 已停用
                        if u.get("role") and u["role"] != data.get("r"):
                            data = dict(data)  # 角色降级/变更：以库中当前角色为准
                            data["r"] = u["role"]
                        # 改密失效：令牌签发时的 epoch 与当前用户 epoch 不符即拒绝
                        # （历史令牌无 e 声明 -> 0，用户未改密时同为 0，不会误杀）。
                        tok_epoch = data.get("e", 0) or 0
                        cur_epoch = u.get("password_epoch", 0) or 0
                        if tok_epoch != cur_epoch:
                            return None
                    else:
                        try:
                            if self.users.count() > 0:
                                return None    # 情形 c：已删除用户的残留令牌
                        except Exception:
                            pass               # 情形 a/b：放行
            return data
        except Exception:
            return None

    def can(self, role: str, perm: str) -> bool:
        allowed = ROLES.get(role, [])
        if "*" in allowed or perm in allowed:
            return True
        # 通配：拥有 "models:write" 亦视为拥有 "models:read"
        base = perm.split(":")[0]
        return f"{base}:write" in allowed and perm == f"{base}:read"


# ----------------------------- 审计日志 -----------------------------
class AuditLog:
    def __init__(self, path: str = "data/zhishu_audit.db", enable: bool = True,
                 redactor=None):
        self.enable = enable
        # 落库前对 detail 做 PII 脱敏（合规要求）；redactor 为 None 时原样存储。
        self.redactor = redactor
        if not enable:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                user TEXT, action TEXT, detail TEXT, ip TEXT
            )"""
        )
        self.conn.commit()

    def log(self, user: str, action: str, detail: str = "", ip: str = ""):
        if not self.enable:
            return
        detail_out = self.redactor.redact(detail) if (self.redactor and detail) else detail
        self.conn.execute(
            "INSERT INTO audit (user, action, detail, ip) VALUES (?,?,?,?)",
            (user, action, detail_out, ip),
        )
        self.conn.commit()

    def recent(self, limit: int = 100) -> list:
        if not self.enable:
            return []
        rows = self.conn.execute(
            "SELECT ts, user, action, detail, ip FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"ts": r[0], "user": r[1], "action": r[2], "detail": r[3], "ip": r[4]} for r in rows]
