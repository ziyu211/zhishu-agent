"""上传文件辅助：带大小上限的分块读取，防止一次性读入导致内存/磁盘耗尽。"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 默认上限 100MB


async def read_upload_limited(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """分块读取上传文件，超过上限立即拒绝（413）。避免 `await file.read()` 全量入内存。"""
    data = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大，上传上限为 {max_bytes // (1024 * 1024)}MB",
            )
    return bytes(data)
