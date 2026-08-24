"""下载链接兜底护栏（Download-link guardrail）。

问题背景：智枢为内网 / 本地一体化部署，用户浏览器与系统同源，工具返回的
``/media/...`` 链接在用户端**即是可点击下载的链接**。但模型常把「下载链接」误读为
「公网 / 外网可访问链接」，于是在确实已生成文件、工具也返回了 ``/media`` 链接的
情况下，仍自创诸如「系统在内网沙箱，无法生成外网可点击下载链接，请联系管理员从沙箱
路径获取」的搪塞话术，并把真实链接吞掉，造成用户体验断裂。

本模块作为**最后兜底**：当本轮对话中工具确实产出了 ``/media`` 链接，但模型最终回复
既没有透传任何链接、又含有搪塞特征时，自动清除搪塞语句，并把真实链接强制补回，
确保用户一定能拿到可点击下载链接。

v1.0.39 新增 **MEDIA: 发布协议**（对标 Hermes extract_media）：模型在回复中输出
``MEDIA:/abs/path`` 标签即声明「该文件需要交付给用户」，本模块解析标签、把真实存在的
文件发布为 /media 链接并替换标签——任何模型显式声明的路径（含写在 cwd 之外、快照
差分捕获不到的）都能交付，模型侧零学习成本。

设计为纯函数，无外部依赖，便于单元测试。
"""
from __future__ import annotations

import os
import re
from typing import List, Tuple

# /media/... 链接（相对 URL，前端会基于当前 origin 拼接）
_MEDIA_RE = re.compile(r"/media/[^\s)\]\"'<>]+", re.IGNORECASE)

# MEDIA: 发布协议标签（对标 Hermes）：MEDIA:/abs/path
_MEDIA_TAG_RE = re.compile(r"(?i)\bMEDIA:\s*([^\s)\]\"'<>]+)")

# 搪塞特征：模型把内网 /media 链接误读为需要公网 / 外网而推脱。
# 注意：这些模式使用普通字符串拼接（非 f-string / .format），因为模式内部含
# 正则量词 {0,14} 等花括号，不能被当作占位符解析。
_EVASION_PATTERNS = [
    # 模型以「环境限制说明」表格/列表形式搪塞（新变体）
    r"当前环境限制说明",
    r"无\s*Web\s*服务器",
    r"无\s*文件下载服务",
    r"没有配置\s*HTTP\s*服务",
    r"无法生成.{0,16}可访问的下载\s*URL",
    r"内网隔离",
    r"文件保存在沙箱内.{0,16}外部无法访问",
    # 原有搪塞话术
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


def _is_md_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") or re.match(r"^[\|\-\:\s]+$", s) is not None


def _is_html_table_line(line: str) -> bool:
    s = line.strip().lower()
    return (
        s.startswith("<table")
        or s.startswith("</table>")
        or s.startswith("<thead")
        or s.startswith("</thead>")
        or s.startswith("<tbody")
        or s.startswith("</tbody>")
        or s.startswith("<tr")
        or s == "</tr>"
        or s.startswith("<td")
        or s.startswith("</td>")
        or s.startswith("<th")
        or s.startswith("</th>")
    )


def _clean_evasion_blocks(text: str) -> str:
    """删除含搪塞特征的段落 / 表格块。

    比 _clean_evasion_sentences 更粗粒度：能处理模型生成的 Markdown / HTML
    「环境限制说明」表格——只要表格内部任意行含搪塞特征，整段表格一起删除。
    普通文本行仍按整行删除。
    """
    if not text:
        return text
    lines = text.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 空行直接保留，作为段落边界
        if stripped == "":
            out_lines.append(line)
            i += 1
            continue
        # 表格行：收集连续表格区域，整体判断是否含搪塞
        if _is_md_table_line(line) or _is_html_table_line(line):
            table_lines: list[str] = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.strip() == "":
                    break
                if not (_is_md_table_line(next_line) or _is_html_table_line(next_line)):
                    break
                table_lines.append(next_line)
                j += 1
            table_text = "\n".join(table_lines)
            if not _EVASION_RE.search(table_text):
                out_lines.extend(table_lines)
            # 含搪塞则整段丢弃
            i = j
            continue
        # 普通行：含搪塞即删除
        if _EVASION_RE.search(line):
            i += 1
            continue
        out_lines.append(line)
        i += 1
    out = "\n".join(out_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def guard_download_links(final: str, media_links: List[str]) -> tuple[str, bool]:
    """若模型搪塞下载链接，则清除搪塞句并把真实 /media 链接强制补回。

    返回 (new_final, triggered)。
    """
    if not needs_guard(final, media_links):
        return final, False
    cleaned = _clean_evasion_blocks(final)
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


# ── MEDIA: 发布协议（v1.0.39，对标 Hermes extract_media）────────────────
# 模型在回复中输出 MEDIA:/abs/path 标签即声明「该文件要交付给用户」。本函数把标签
# 替换为 /media 下载链接（文件真实存在时发布；已落在媒体根则直接改写；不存在则
# 保留原文并附说明，绝不编造链接）。


def extract_media_tags(text: str) -> List[str]:
    """提取回复中的所有 MEDIA: 路径（去重保序）。"""
    seen: List[str] = []
    for m in _MEDIA_TAG_RE.finditer(text or ""):
        p = m.group(1).strip()
        if p and p not in seen:
            seen.append(p)
    return seen


def process_media_tags(
    text: str,
    media,
    owner: str,
    *,
    media_root: str = "",
    sandbox_root: str = "",
) -> Tuple[str, List[Tuple[str, str]]]:
    """把回复中的 MEDIA:/path 标签替换为 /media 下载链接。

    返回 (new_text, [(文件名, /media/... URL), ...])。
      * 路径已落在媒体根（media_root）→ 直接改写为 /media 链接（零拷贝）；
      * 其它真实存在的路径 → 拷贝发布（media.save_file）；
      * 不存在 / 无媒体库 → 保留原文（绝不编造链接）。
    """
    if not text or media is None:
        return text or "", []
    media_root = os.path.abspath(media_root) if media_root else ""
    replaced = text
    published: List[Tuple[str, str]] = []
    seen_urls: set = set()

    for path in extract_media_tags(replaced):
        ap = os.path.abspath(path)
        try:
            is_file = os.path.isfile(ap)
            fsize = os.path.getsize(ap) if is_file else 0
        except OSError:
            is_file, fsize = False, 0
        if not is_file or fsize == 0:
            # 不存在：替换标签为说明文本，避免把死路径回显给用户
            replaced = replaced.replace(
                f"MEDIA:{path}", f"[文件不存在，未生成下载链接: {path}]", 1)
            replaced = replaced.replace(
                f"MEDIA: {path}", f"[文件不存在，未生成下载链接: {path}]", 1)
            continue
        # (a) 已在媒体根 → 直接改写
        if media_root and (ap == media_root or ap.startswith(media_root + os.sep)):
            rel = os.path.relpath(ap, media_root).replace(os.sep, "/")
            url = "/media/" + rel
        else:
            # (b) 拷贝发布
            try:
                url = media.save_file(ap, kind="file", owner=owner or None)
            except Exception as e:  # noqa: BLE001
                replaced = replaced.replace(
                    f"MEDIA:{path}", f"[发布失败: {e}]", 1)
                continue
        if url not in seen_urls:
            seen_urls.add(url)
            published.append((os.path.basename(ap), url))
        link = f"[{os.path.basename(ap)}]({url})"
        replaced = replaced.replace(f"MEDIA:{path}", link, 1)
        replaced = replaced.replace(f"MEDIA: {path}", link, 1)
    return replaced, published
