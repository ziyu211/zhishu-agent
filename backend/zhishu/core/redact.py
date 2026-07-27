"""智枢智能体 —— 数据脱敏模块（合规补充，对标 Hermes redact/message_sanitization）。

设计要点：
  * 纯正则实现，零外部依赖，离线可用、内网安全。
  * 默认遮蔽手机/身份证/邮箱/银行卡/身份证/常见敏感字段值；
    可扩展自定义正则与「保留前后 N 位」的掩码策略。
  * 落库（审计日志）与对外输出前调用，满足等保/合规对 PII 的要求。
  * 所有方法均吞掉异常，脱敏失败不影响主流程（最坏情况原样返回）。
"""
from __future__ import annotations

import re
from typing import Optional

# 常见 PII 检测器：name -> (正则, 掩码保留前缀长度, 掩码保留后缀长度)
_PATTERNS: dict[str, tuple[re.Pattern, int, int]] = {
    # 中国大陆手机号
    "phone": (re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"), 3, 2),
    # 身份证（18 位或 15 位）
    "idcard": (re.compile(r"(?<!\d)(\d{17}[\dXx]|\d{15})(?!\d)"), 4, 2),
    # 电子邮箱
    "email": (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), 1, 0),
    # 银行卡（16~19 位连续数字，非手机号/身份证）
    "bankcard": (re.compile(r"(?<!\d)(\d{16,19})(?!\d)"), 4, 4),
}

# 自定义键值对中的敏感字段名（命中则对值做强遮蔽）
_SENSITIVE_KEY_HINTS = (
    "password", "passwd", "pwd", "secret", "token", "apikey", "api_key",
    "ak", "sk", "access_key", "private_key", "证件", "身份证", "银行卡",
    "手机号", "手机", "电话", "邮箱", "密码", "密钥", "令牌",
)


class Redactor:
    """PII 脱敏器：对文本/字典做正则遮蔽。"""

    def __init__(self, enabled: bool = True, extra_patterns: Optional[dict] = None):
        self.enabled = enabled
        self.patterns = dict(_PATTERNS)
        if extra_patterns:
            self.patterns.update(extra_patterns)

    @staticmethod
    def _mask(value: str, keep_head: int, keep_tail: int) -> str:
        n = len(value)
        if n <= keep_head + keep_tail:
            return "*" * n
        # 注意：keep_tail=0 时必须用 value[n-keep_tail:] 而非 value[-keep_tail:]，
        # 否则 -0 == 0 会返回整个原串，导致尾部脱敏失效（如邮箱）。
        return value[:keep_head] + "*" * (n - keep_head - keep_tail) + value[n - keep_tail:]

    def redact(self, text: str) -> str:
        """脱敏一段文本；返回遮蔽后的副本。"""
        if not self.enabled or not text:
            return text
        try:
            out = text
            for _name, (pat, head, tail) in self.patterns.items():
                out = pat.sub(lambda m: self._mask(m.group(0), head, tail), out)
            return out
        except Exception:
            return text

    def redact_dict(self, payload: dict, *, redact_keys: bool = True) -> dict:
        """脱敏字典（常用于审计 detail / 请求体）。

        - redact_keys=True 时，键名命中敏感提示的字段值整体遮蔽。
        - 字符串值走正则脱敏；非字符串值保持原样。
        """
        if not self.enabled or not isinstance(payload, dict):
            return payload
        try:
            out = {}
            for k, v in payload.items():
                key_str = str(k).lower()
                is_sensitive_key = any(h in key_str for h in _SENSITIVE_KEY_HINTS)
                if isinstance(v, str):
                    if is_sensitive_key:
                        out[k] = "*" * max(4, min(len(v), 12))
                    else:
                        out[k] = self.redact(v)
                elif isinstance(v, dict):
                    out[k] = self.redact_dict(v, redact_keys=redact_keys)
                else:
                    out[k] = v
            return out
        except Exception:
            return payload

    def redact_json(self, raw: str) -> str:
        """尝试按 JSON 解析后脱敏再序列化；非 JSON 则按文本脱敏。"""
        if not self.enabled or not raw:
            return raw
        try:
            obj = __import__("json").loads(raw)
            return __import__("json").dumps(self.redact_dict(obj), ensure_ascii=False)
        except Exception:
            return self.redact(raw)


# 进程级默认脱敏器（由上下文按 security.enable_redact 初始化）
_default: Optional[Redactor] = None


def set_default(redactor: Optional[Redactor]) -> None:
    global _default
    _default = redactor


def get_default() -> Redactor:
    global _default
    if _default is None:
        _default = Redactor(enabled=True)
    return _default


def redact(text: str) -> str:
    return get_default().redact(text)
