"""智枢智能体 —— 多模态产物存储（MediaStore）。

图像/视频生成接口通常只返回**临时 URL**（会过期）。为保证产物持久、可离线回看，
智枢在生成后立即把字节落盘到 data/<store_dir>/，并通过后端 /media 同源托管，
前端拿到的永远是本地稳定 URL（形如 /media/img_xxx.png）。
"""
from __future__ import annotations

import os
import re
import time
import mimetypes
from typing import Optional
from urllib.parse import quote as _urlquote


# ── 媒体 MIME 单一真源（参考 Hermes #34517 事故：多份扩展名→类型列表漂移，
#    导致文件被静默丢失/错误投递）。凡涉及「扩展名→Content-Type」与「可发布类型」
#    判定的地方，一律引用本表，避免第二份定义逐渐失同步。
MEDIA_MIME: dict[str, str] = {
    # 文本 / 数据
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/json; charset=utf-8",
    ".ndjson": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
    ".toml": "application/toml; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    # 富文本 / 文档
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".epub": "application/epub+zip",
    # 图片
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    # 音频
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    # 视频
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    # 压缩 / 归档
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz",
    # 兜底
    ".bin": "application/octet-stream",
}


def media_mime(filename: str) -> str:
    """根据文件名返回 Content-Type（查单一真源表，缺失则回退 mimetypes）。"""
    ext = os.path.splitext(filename)[1].lower()
    return MEDIA_MIME.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def content_disposition(name: str) -> str:
    """构造 RFC 5987 兼容的 ``Content-Disposition: attachment`` 头值。

    同时给出 ASCII 兜底名与 ``filename*=UTF-8''`` 真名，保证中文/特殊字符文件名在
    各类浏览器下均以正确名称保存（参考 Hermes Web UI download.ts 的双写写法）。
    """
    ascii_name = name.encode("ascii", "ignore").decode() or "download"
    return "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
        ascii_name, _urlquote(name, safe="")
    )


class MediaStore:
    def __init__(self, root: str, url_prefix: str = "/media"):
        self.root = root
        self.url_prefix = url_prefix.rstrip("/")
        os.makedirs(self.root, exist_ok=True)

    def _new_name(self, kind: str, ext: str) -> str:
        ts = time.strftime("%Y%m%d-%H%M%S")
        rand = os.urandom(3).hex()
        ext = ext.lstrip(".") or "bin"
        return f"{kind}_{ts}_{rand}.{ext}"

    @staticmethod
    def _slugify_name(name: str, kind: str = "file") -> str:
        """把任意文件名安全化为合法落盘名：保留扩展名，主体只留字母/数字/下划线/汉字/连字符。"""
        name = os.path.basename(name)
        stem, ext = os.path.splitext(name)
        ext = (ext.lstrip(".").lower() or "bin")[:10]
        # 仅保留字母数字下划线连字符、中文与小数点（主体内的点常见于版本号/网段），
        # 路径分隔符不在白名单中会被替换，杜绝路径穿越
        safe = re.sub(r"[^\w一-龥\-\.]", "_", stem)
        safe = safe.strip("_-") or kind
        return f"{safe[:80]}.{ext}"

    @staticmethod
    def _unique_name(directory: str, name: str) -> str:
        """在 directory 内确保 name 不冲突（冲突则追加序号）。"""
        cand = name
        i = 1
        while os.path.exists(os.path.join(directory, cand)):
            stem, ext = os.path.splitext(name)
            cand = f"{stem}_{i}{ext}"
            i += 1
        return cand

    def save_file(self, src_path: str, kind: str = "file",
                  owner: Optional[str] = None) -> str:
        """流式保留**原文件名（slug 化）**把磁盘文件落盘到媒体库，返回 /media/... URL。

        与 save_bytes（随机名）不同，本方法保留人类可读的原始文件名，使前端下载时
        文件名与用户期待一致；并自动做冲突去重、大小写与特殊字符归一，杜绝路径穿越。
        """
        if not os.path.isfile(src_path):
            raise FileNotFoundError(src_path)
        name = self._slugify_name(os.path.basename(src_path), kind)
        if owner:
            target_dir = os.path.join(self.root, owner)
            os.makedirs(target_dir, exist_ok=True)
            url_dir = f"{self.url_prefix}/{owner}"
        else:
            target_dir = self.root
            url_dir = self.url_prefix
        final = self._unique_name(target_dir, name)
        dest = os.path.join(target_dir, final)
        # 流式拷贝（避免超大文件一次性读入内存）
        with open(src_path, "rb") as src, open(dest, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        return f"{url_dir}/{final}"

    def save_bytes(self, data: bytes, kind: str = "img", ext: str = "png",
                   owner: Optional[str] = None) -> str:
        """保存字节，返回可同源访问的 URL。

        owner 非空时按 /media/<owner>/<name> 隔离存储，配合 /media 鉴权网关实现
        多租户生成产物越权防护；owner 为空则维持历史平铺路径（兼容旧数据）。
        """
        name = self._new_name(kind, ext)
        if owner:
            owner_dir = os.path.join(self.root, owner)
            os.makedirs(owner_dir, exist_ok=True)
            path = os.path.join(owner_dir, name)
            with open(path, "wb") as f:
                f.write(data)
            return f"{self.url_prefix}/{owner}/{name}"
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(data)
        return f"{self.url_prefix}/{name}"

    @staticmethod
    def guess_ext(url: str, fallback: str = "png", content_type: Optional[str] = None) -> str:
        """从 URL 或 content-type 推断扩展名。"""
        if content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                return guessed.lstrip(".")
        base = url.split("?")[0].split("#")[0]
        _, dot, ext = base.rpartition(".")
        if dot and 1 <= len(ext) <= 5 and ext.isalnum():
            return ext.lower()
        return fallback


def resolve_media_fallback(file_path: str, user: Optional[dict],
                          root: str, max_bytes: int) -> Optional[str]:
    """精确路径 404 时的容错回退：在用户授权目录内按文件名找回真实文件。

    覆盖三类「链接与磁盘不一致」导致的 404：
      ① 链接缺 owner 段（如 /media/foo.txt，实际存于 /media/<owner>/foo.txt）；
      ② 缺扩展名 / 空格未编码（如 /media/.../fw_log，实际为 fw_log (0626).csv）；
      ③ 历史对话里格式偏差的旧链接。

    授权范围（防越权）：
      * 普通用户：仅自身 owner 目录（含 attachments/<owner>）；
      * admin：可搜全部 owner 目录（admin 本就能看全部）。
    命中后返回磁盘绝对路径；查无此文件返回 None（由调用方诚实 404）。
    """
    if not user:
        return None
    bn = os.path.basename(file_path)
    if not bn or bn in (".", ".."):
        return None
    owners = [user.get("u")] if user.get("u") else []
    if (user.get("r") or "") == "admin":
        try:
            owners = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        except OSError:
            owners = owners or []
    dirs: list[str] = []
    for o in set(owners):
        if not o:
            continue
        dirs.append(os.path.join(root, o))
        dirs.append(os.path.join(root, "attachments", o))
    # 归一化查询名：去扩展名 + 去 _N 去重后缀，用于前缀匹配（修缺扩展名/空格类偏差）
    stem = bn.rsplit(".", 1)[0] if "." in bn else bn
    stem_core = re.sub(r"_\d+$", "", stem)
    best, best_rank = None, None
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for dp, _, fns in os.walk(d):
            for fn in fns:
                fp = os.path.join(dp, fn)
                if not os.path.isfile(fp):
                    continue
                if fn == bn:
                    rank = 0
                elif stem_core and fn.startswith(stem_core):
                    rank = 1
                else:
                    continue
                try:
                    if os.path.getsize(fp) > max_bytes:
                        continue
                except OSError:
                    continue
                if best_rank is None or rank < best_rank:
                    best, best_rank = fp, rank
    return best
