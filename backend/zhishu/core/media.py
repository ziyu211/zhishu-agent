"""智枢智能体 —— 多模态产物存储（MediaStore）。

图像/视频生成接口通常只返回**临时 URL**（会过期）。为保证产物持久、可离线回看，
智枢在生成后立即把字节落盘到 data/<store_dir>/，并通过后端 /media 同源托管，
前端拿到的永远是本地稳定 URL（形如 /media/img_xxx.png）。
"""
from __future__ import annotations

import os
import time
import mimetypes
from typing import Optional


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

    def save_bytes(self, data: bytes, kind: str = "img", ext: str = "png") -> str:
        """保存字节，返回可同源访问的 URL（/media/<name>）。"""
        name = self._new_name(kind, ext)
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
