"""工具产物自动发布（Artifact auto-publish）。

背景：前序版本让 code_exec / terminal_run / file_write 通过可选参数
（save_output / downloadable，默认 false）来决定是否把生成文件落媒体库。模型在文件
处理流程中从不主动传 true，于是始终只返回内部路径，并自创「无法生成下载链接」话术，
用户体验断裂。

本模块提供通用能力：在工具执行**前后**对工作区目录做快照 / 差分，把本次**新增或修改**
的文件自动落盘到媒体库并返回 /media/... 下载链接，无需模型显式干预。

设计要点：
  * 差分基于 (mtime, size) 快照：执行前记一份，执行后比对，只发布「新出现或发生变化」
    的文件，绝不把历史残留文件当成产物。
  * 大小 / 数量上限：单文件超 _MAX_BYTES 跳过并提示；单轮最多发布 _MAX_FILES 个，防刷屏。
  * 排除临时产物：以 _EXCLUDE_PREFIXES 开头的临时脚本 / 输出目录自动忽略。
  * 依赖 ToolContext.media（MediaStore.save_file），未注入媒体库时直接降级为空串。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from ...media import MediaStore  # artifacts 位于 zhishu.core.tools.builtins，'...' 即 zhishu.core
from ...agent.download_guard import find_leaked_paths, extract_media_links

# 单文件大小上限（默认 100MB，超出跳过并提示）
_MAX_BYTES = 100 * 1024 * 1024
# 单轮最多发布的文件数，超出截断（保护消息体 & 存储）
_MAX_FILES = 20
# 这些前缀的文件视为本系统临时产物，不纳入发布
_EXCLUDE_PREFIXES = ("zh_out_", "zh_code_", ".zh_")

# 任意绝对路径 + 合法扩展名（兜底捕获 publish_diff 快照范围之外的真实产物，
# 例如写到 /tmp、挂载卷、data/generated/attachments/... 等处的文件）。
# 同时兼容 Linux（/ 起始）与 Windows（C:\ 盘符）绝对路径；排除 /media/ 合法下载链接
# （避免把已发布的 URL 当成泄漏路径重新处理）。
_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)[^\s)\]\"\'<>]*\.(?:txt|csv|xlsx?|xls|json|md|pdf|docx?|png|jpe?g|gif|mp3|wav|zip|log|tsv|pptx?|html?)",
    re.IGNORECASE,
)


def snapshot(root: str) -> dict[str, tuple[float, int]]:
    """记录 root 下所有现有文件的 (mtime, size)，键为相对路径。

    用于执行前基线；与执行后快照比对得到「本次新增 / 修改」集合。空目录或异常返回空 dict。
    """
    snap: dict[str, tuple[float, int]] = {}
    root = os.path.abspath(root)
    try:
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                snap[rel] = (st.st_mtime, st.st_size)
    except OSError:
        pass
    return snap


def _is_excluded(rel: str) -> bool:
    return os.path.basename(rel).startswith(_EXCLUDE_PREFIXES)


def publish_diff(
    root: str,
    before: dict[str, tuple[float, int]],
    media: Optional[MediaStore],
    owner: str,
    *,
    max_files: int = _MAX_FILES,
    max_bytes: int = _MAX_BYTES,
    label: str = "生成的可下载文件",
) -> str:
    """对 root 做差分，把新增 / 修改的文件发布到媒体库，返回要追加的 Markdown 片段。

    返回形如 ``\\n\\n[生成的可下载文件]:\\n- [name](/media/...)`` 的片段；
    无新文件且无需提示时返回空串。media 为 None 或 root 不可用时同样返回空串（优雅降级）。
    """
    if media is None:
        return ""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return ""
    after = snapshot(root)
    published: list[tuple[str, str]] = []
    skipped = 0
    for rel, (mtime, size) in after.items():
        if rel in before and before[rel] == (mtime, size):
            continue  # 未变化，跳过
        if _is_excluded(rel):
            continue
        fp = os.path.join(root, rel)
        if not os.path.isfile(fp) or size == 0:
            continue
        if size > max_bytes:
            skipped += 1
            continue
        try:
            url = media.save_file(fp, kind="file", owner=owner)
        except Exception:
            continue
        published.append((os.path.basename(rel), url))
        if len(published) >= max_files:
            break
    if not published and skipped == 0:
        return ""
    lines = ["", "", f"[{label}]（以下链接真实有效，用户在浏览器点击即可下载；你必须原样、完整展示给用户，"
            f"禁止改写为『无法生成链接』『联系管理员』或『只给路径』等说法）："]
    for name, url in published:
        lines.append(f"- [{name}]({url})")
    if skipped:
        limit_mb = max_bytes // 1024 // 1024
        lines.append(f"- （另有 {skipped} 个文件超过大小上限 {limit_mb}MB，未发布）")
    return "\n".join(lines)


# ── 引用路径重发布（publish_diff 的互补能力）──
# 背景：publish_diff 基于「执行前后 cwd 快照差分」，只能捕获落在 sandbox/<owner>
# 工作区与 ZHISHU_OUTPUT_DIR 内、且执行期间新增/修改的文件。若模型把产物写到这两个
# 目录之外（如直接写入 data/generated/attachments/<owner>/...、/tmp、挂载卷等），
# 文件虽真实存在，却永远生成不了 /media 链接 —— 这正是「生成报告后无下载链接」的根因。
#
# 本函数为这类「漏网」文件兜底：从模型回复文本里抽取绝对文件路径，对真实存在的文件：
#   (a) 已落在媒体根 media_root（= data_dir/generated）内 → 直接改写为 /media/<rel>
#       链接（文件本就由媒体库托管，无需拷贝，零重复）；
#   (b) 落在 sandbox_root / out_dir 内或其它任意位置但真实存在 → 拷贝到媒体库发布。
# 返回 (改写后的文本, [(name, url), ...])，无媒体库或无可发布文件时返回原文本与空列表。
def publish_referenced_paths(
    text: str,
    media: Optional[MediaStore],
    owner: str,
    media_root: Optional[str],
    sandbox_root: Optional[str],
    out_dir: Optional[str] = None,
    *,
    max_files: int = _MAX_FILES,
    max_bytes: int = _MAX_BYTES,
) -> Tuple[str, List[Tuple[str, str]]]:
    """从模型回复里抽取绝对文件路径，把真实存在的文件统一发布为 /media 链接。

    与 publish_diff（基于 cwd 快照差分）互补：本函数处理「模型把文件写到 cwd/输出目录
    之外、却又把内部绝对路径回显给用户」的情形，确保任意真实产物都能出 /media 链接。

    规则：
      (a) 路径已落在媒体根 media_root（data_dir/generated）内 → 直接改写为
          /media/<rel> 链接，无需拷贝（文件本就由媒体库托管）；
      (b) 落在 sandbox_root / out_dir 内或任意其它位置但真实存在 → 拷贝到媒体库发布。
    返回 (改写后的文本, [(name, url), ...])。
    """
    if media is None or not text:
        return text, []
    media_root = os.path.abspath(media_root) if media_root else None
    sb_root = os.path.abspath(sandbox_root) if sandbox_root else None
    out_root = os.path.abspath(out_dir) if out_dir else None

    # 候选路径：优先取下载护栏识别的泄漏路径（与 download_guard 口径一致），
    # 再补充任意绝对路径（排除 /media 合法链接），尽量兜住更多角落。
    candidates: set[str] = set(find_leaked_paths(text))
    for m in _ABS_PATH_RE.finditer(text or ""):
        p = m.group(0)
        if p.startswith("/media/"):
            continue
        candidates.add(p)
    if not candidates:
        return text, []

    # 长路径优先替换，避免短路径作为子串被提前替换而错位
    replaced = text
    recovered: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=len, reverse=True):
        ap = os.path.abspath(path)
        try:
            is_file = os.path.isfile(ap)
            fsize = os.path.getsize(ap) if is_file else 0
        except OSError:
            continue
        if not is_file or fsize == 0:
            continue
        # (a) 已在媒体根：改写为 /media 链接（文件本就由媒体库托管，零拷贝零重复）
        if media_root and (ap == media_root or ap.startswith(media_root + os.sep)):
            rel = os.path.relpath(ap, media_root).replace(os.sep, "/")
            url = "/media/" + rel
            recovered.append((os.path.basename(ap), url))
            replaced = replaced.replace(path, f"[{os.path.basename(ap)}]({url})")
            continue
        # (b) 拷贝发布（sandbox / out_dir / 其它真实文件，尽力而为）
        try:
            url = media.save_file(ap, kind="file", owner=owner)
        except Exception:
            continue
        if url in seen:
            continue
        seen.add(url)
        recovered.append((os.path.basename(ap), url))
        replaced = replaced.replace(path, f"[{os.path.basename(ap)}]({url})")
        if len(recovered) >= max_files:
            break
    return replaced, recovered


_LINKED_NAME_RE = re.compile(r"\[([^\]]+)\]\((/media/[^)]+)\)")


def _already_linked_names(text: str) -> set:
    """收集文本中已出现的下载链接文件名（去重参考，避免重复追加）。"""
    names: set = set()
    for m in _LINKED_NAME_RE.finditer(text or ""):
        names.add(m.group(1).strip().lower())
    for u in extract_media_links(text or ""):
        names.add(os.path.basename(u).split("?")[0].lower())
    return names


def append_unique_links(
    text: str,
    refs: List[Tuple[str, str]],
    *,
    max_total: int = _MAX_FILES,
    label: str = "本次生成的可下载文件",
) -> str:
    """把 refs 中「文件名尚未在文本里出现」的下载链接去重追加为 Markdown 列表。

    用于 code_exec / terminal_run 在 publish_diff 之后补发布「写在 cwd 之外」的产物，
    避免与 publish_diff 已发布的链接重复刷屏。
    """
    if not refs:
        return text
    have = _already_linked_names(text)
    new_pairs = [(n, u) for n, u in refs if n.lower() not in have]
    if not new_pairs:
        return text
    lines = ["", "", f"[{label}]:", ]
    for n, u in new_pairs[:max_total]:
        lines.append(f"- [{n}]({u})")
    return (text + "\n".join(lines)).strip()
