"""下载链接兜底护栏（Download-link guardrail）。

问题背景：智枢为内网 / 本地一体化部署，用户浏览器与系统同源，工具返回的
``/media/...`` 链接在用户端**即是可点击下载的链接**。但模型常把「下载链接」误读为
「公网 / 外网可访问链接」，于是在确实已生成文件、工具也返回了 ``/media`` 链接的
情况下，仍自创诸如「系统在内网沙箱，无法生成外网可点击下载链接，请联系管理员从沙箱
路径获取」的搪塞话术，并把真实链接吞掉，造成用户体验断裂。

本模块作为**最后兜底**：当本轮对话中工具确实产出了 ``/media`` 链接，但模型最终回复
既没有透传任何链接、又含有搪塞特征时，自动清除搪塞语句，并把真实链接强制补回，
确保用户一定能拿到可点击下载链接。

设计为纯函数，无外部依赖，便于单元测试。
"""
from __future__ import annotations

import re
from typing import List

# /media/... 链接（相对 URL，前端会基于当前 origin 拼接）
_MEDIA_RE = re.compile(r"/media/[^\s)\]\"'<>]+", re.IGNORECASE)

# 搪塞特征：模型把内网 /media 链接误读为需要公网 / 外网而推脱。
# 注意：这些模式使用普通字符串拼接（非 f-string / .format），因为模式内部含
# 正则量词 {0,14} 等花括号，不能被当作占位符解析。
_EVASION_PATTERNS = [
    r"内网.{0,14}(沙箱|环境).{0,14}(无法|不能|不支持|无法生成|不能生成).{0,10}(下载|链接)",
    r"沙箱.{0,14}(环境|内网).{0,14}(无法|不能|不支持).{0,10}(下载|链接)",
    r"无法生成.{0,10}下载链接",
    r"不支持.{0,8}可点击.{0,8}下载",
    r"不支持.{0,8}生成.{0,8}下载链接",
    r"无法.{0,8}(提供|生成|给出).{0,8}(可点击|可下载|下载).{0,8}链接",
    r"请联系管理员.{0,16}(获取|下载|拿|得到|拿到|提取|索取).{0,8}(文件|资料|链接|附件)",
    r"请联系管理员.{0,12}(沙箱|本地路径|路径|内网)",
    r"只能.{0,6}(给|提供|给到).{0,6}(沙箱路径|本地路径|路径)",
    r"外网.{0,8}下载链接",
]
_EVASION_RE = re.compile(
    "|".join("(?:" + p + ")" for p in _EVASION_PATTERNS), re.IGNORECASE
)


def extract_media_links(text: str) -> List[str]:
    """从文本中提取所有 /media/... 链接（去重保序由调用方处理）。"""
    return _MEDIA_RE.findall(text or "")


def _has_media_link(text: str) -> bool:
    return bool(_MEDIA_RE.search(text or ""))


def _has_evasion(text: str) -> bool:
    return bool(_EVASION_RE.search(text or ""))


def needs_guard(final: str, media_links: List[str]) -> bool:
    """判断是否需要触发兜底。

    触发条件（三者同时满足，避免误伤正常回复）：
      1. 本轮工具确实产出了 /media 链接（media_links 非空）；
      2. 模型最终回复里**没有**透传任何 /media 链接；
      3. 模型最终回复含有搪塞特征（把内网链接当成外网推脱）。
    """
    if not media_links:
        return False
    if _has_media_link(final):
        return False  # 已经透传了链接，无需兜底
    return _has_evasion(final)


def _clean_evasion_sentences(text: str) -> str:
    """删除含搪塞特征的整句，保留其余内容。按句末标点 / 换行断句。"""
    parts = re.split(r"(?<=[。！？；\.\n])", text or "")
    kept = [p for p in parts if not _EVASION_RE.search(p)]
    out = "".join(kept).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)  # 清理删除后产生的多余空行
    return out


def guard_download_links(final: str, media_links: List[str]) -> tuple[str, bool]:
    """若模型搪塞下载链接，则清除搪塞句并把真实 /media 链接强制补回。

    返回 (new_final, triggered)。
    """
    if not needs_guard(final, media_links):
        return final, False
    cleaned = _clean_evasion_sentences(final)
    seen = set()
    uniq: List[str] = []
    for u in media_links:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    block = (
        "\n\n---\n\n📎 **本次生成的可下载文件（点击即可下载）：**\n"
        + "\n".join(f"- {u}" for u in uniq)
    )
    new_final = (cleaned + block).strip() if cleaned else block.strip()
    return new_final, True


# ── 内部绝对路径泄漏处理 ──
# 模型把文件写到内部工作区（受限 cwd）后，会错误地把磁盘绝对路径回显给用户，
# 例如 /app/backend/data/sandbox/xxx.csv、/app/backend/data/output/xxx.csv。
# 这些路径用户浏览器无法访问，必须剥离；若文件真实存在，则重新发布为 /media 链接。
#
# 重要：合法下载链接形如 /media/<owner>/xxx.csv，当 owner 段恰好为 root / dataops 等时，
# 会与上述内部路径前缀（/root、/data…）重叠。因此必须先保护 /media/... 链接，
# 再在「链接之外」的文本里查找泄漏，避免把合法下载链接误删/误判。
_LEAK_RE = re.compile(
    r"(?:/app|/data|/var|/root|/home|/backend|/sandbox|/opt|/usr|/tmp|/etc)"
    r"[^\s)\]\"\'<>]*\.(?:txt|csv|xlsx?|xls|json|md|pdf|docx?|png|jpe?g|gif|mp3|wav|zip|log|tsv|pptx?|html?)",
    re.IGNORECASE,
)


def _media_spans(text: str) -> List[tuple]:
    """返回文本中所有 /media/... 链接的 (start, end) 区间。"""
    return [(m.start(), m.end()) for m in _MEDIA_RE.finditer(text or "")]


def _inside_media(spans: List[tuple], s: int, e: int) -> bool:
    return any(s >= ms and e <= me for ms, me in spans)


def find_leaked_paths(text: str) -> List[str]:
    """从文本中提取疑似泄漏的内部绝对文件路径（排除 /media/ 下载链接内部）。"""
    spans = _media_spans(text or "")
    out = []
    for m in _LEAK_RE.finditer(text or ""):
        if _inside_media(spans, m.start(), m.end()):
            continue  # 属于合法 /media 下载链接的一部分，跳过
        out.append(m.group(0))
    return out


def strip_leaked_paths(text: str) -> str:
    """删除文本中泄漏的内部绝对路径，并清理因此产生的孤立标题/空行。
    保护 /media/... 下载链接，避免 owner 段（如 root/dataops）被误判为泄漏路径。"""
    spans = _media_spans(text or "")
    out: List[str] = []
    last = 0
    for m in _LEAK_RE.finditer(text or ""):
        if _inside_media(spans, m.start(), m.end()):
            out.append(text[last:m.end()])   # 属合法 /media 链接，原样保留
        else:
            out.append(text[last:m.start()])  # 真实泄漏，删除
        last = m.end()
    out.append(text[last:])
    result = "".join(out)
    # 清理因删除路径而孤立的「获取方式 / 获取文件」类标题（可带 emoji）
    result = re.sub(
        r"^\s*[⬇️▼▶▷➡️🔗↓]*\s*获取(方式|文件)\s*[:：]?\s*$",
        "", result, flags=re.MULTILINE,
    )
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def strip_evasion(text: str) -> str:
    """删除含搪塞特征的整句（公开封装，供护栏在剥离泄漏路径前清理话术）。"""
    return _clean_evasion_sentences(text or "")
