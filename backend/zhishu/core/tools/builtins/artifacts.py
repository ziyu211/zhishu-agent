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
from typing import Optional

from ...media import MediaStore  # artifacts 位于 zhishu.core.tools.builtins，'...' 即 zhishu.core

# 单文件大小上限（默认 100MB，超出跳过并提示）
_MAX_BYTES = 100 * 1024 * 1024
# 单轮最多发布的文件数，超出截断（保护消息体 & 存储）
_MAX_FILES = 20
# 这些前缀的文件视为本系统临时产物，不纳入发布
_EXCLUDE_PREFIXES = ("zh_out_", "zh_code_", ".zh_")


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
