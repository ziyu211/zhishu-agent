"""智枢 —— 图片 / PDF 多模态输入路由（对标 Hermes 的图片与扫描件处理）。

设计原则（与 Hermes 一致：本模块只负责把图片/PDF「喂给模型」的路由，不抽取文字；
  图片/扫描件内的文字提取由 read_file 经内置 OCR 另路完成）：
  1. **上传只落盘、不解析**：附件由 chat/attach 落到 media 目录，本模块只在「发送」时
     决定如何把视觉信息交给模型，不抽取图片/扫描件内的文字（OCR 走 read_file）。
  2. **图片路由 image_input_mode（auto / native / text）**：
       - auto / native：若当前文本模型支持视觉，则把图片以 base64 data URL 作为
         ``image_url`` content part 附到用户消息（native 模式），并附加
         ``[Image attached at: <path>]`` 文本提示；
       - text：模型无视觉能力，仅告知「图片已上传但无法分析」。
     非通用格式（bmp/tiff/heic/avif/ico 等）用 **Pillow 跨平台转码为 PNG**，避免
     provider 返回 415/400（Hermes 同款魔术字节嗅探 + 转码思路，零原生依赖）。
  3. **扫描件 / 需"看"的 PDF：`pdf.attach` 思路 —— 把每页渲染成 150 DPI PNG，
     作为视觉图片喂给模型去"读"，完全不调用 OCR**（上限 50MB / 25 页）。
     文本型 PDF 不渲染（省 token），仅注入上下文提示让 Agent 用 read_file 自取文本层。
  4. **二进制文档（PDF/DOCX/XLSX 等）上下文提示**：注入 "文件已存 `<path>`，请自己
     提取文本再回答，别让用户粘贴内容"（对标 Hermes ``_build_document_context_note``）。

所有依赖（fitz / PIL）均**惰性导入**，缺失时优雅降级（skip 而非崩溃），契合智枢
"可选依赖缺失自动降级"的工程约定。
"""
from __future__ import annotations

import base64
import io
import os
from typing import Optional

# 通用 provider 都接受的图片 MIME（其余需要转码）
_UNIVERSALLY_SUPPORTED_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})

_IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".avif", ".ico", ".svg",
)

# 渲染 / 转码上限（与 Hermes pdf.attach 对齐）
_PDF_ATTACH_MAX_BYTES = 50 * 1024 * 1024   # 单 PDF 渲染产物上限
_PDF_ATTACH_MAX_PAGES = 25                 # 单 PDF 最多渲染页数
_PDF_RENDER_DPI = 150                      # 渲染 DPI（Hermes 同款）

# 单张视觉图片的主动收缩上限（跨平台等效于 Hermes 的响应式收缩：
# 不等 provider 拒绝才缩，而是发送前先保证落在安全区间内，避免 Anthropic 5MB 等硬限制）
_IMG_MAX_SIDE = 2000     # 最长边像素
_IMG_MAX_BYTES = 5 * 1024 * 1024  # 单图编码后字节上限


# --------------------------------------------------------------------------
# MIME 魔术字节嗅探 + 转码
# --------------------------------------------------------------------------
def _sniff_mime_from_bytes(raw: bytes) -> Optional[str]:
    """用魔术字节识别图片真实格式（扩展名不可信）。

    覆盖：png / jpeg / gif / webp / bmp / tiff / heic / heif / avif / ico / svg。
    无法识别返回 None。
    """
    if len(raw) < 12:
        return None
    head = raw[:12]
    # PNG
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # JPEG
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # GIF87a / GIF89a
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # WEBP: RIFF....WEBP
    if head[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    # BMP: BM
    if raw[:2] == b"BM":
        return "image/bmp"
    # TIFF: II*\x00 或 MM\x00*
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    # HEIC / HEIF / AVIF: ....ftyp<brand>
    if head[:4] == b"ftyp":
        brand = raw[8:12]
        if brand[:4] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1"):
            return "image/heic"
        if brand[:4] in (b"avif", b"avis"):
            return "image/avif"
    # ICO
    if raw[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    # SVG（文本格式）
    stripped = raw.lstrip()[:5]
    if stripped[:5] == b"<?xml" or stripped[:4] == b"<svg":
        return "image/svg+xml"
    return None


def _transcode_to_png(raw: bytes) -> bytes:
    """用 Pillow 把任意可解码图片重编码为 PNG（跨平台，无原生二进制依赖）。"""
    from PIL import Image  # 惰性导入（可选依赖）

    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")  # 统一为 RGB，规避调色板/透明通道差异
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fit_image_bytes(data: bytes, mime: str):
    """主动收缩单张图片：最长边不超过 _IMG_MAX_SIDE，编码后体积不超过 _IMG_MAX_BYTES。

    返回 (bytes, mime)。超出时优先等比缩小，仍超则降为 JPEG 并逐步压低质量。
    这是跨平台下对 Hermes「provider 拒绝后再缩小」的等效前置处理，避免硬限制 400。
    """
    try:
        from PIL import Image
    except Exception:
        return data, mime  # 无 Pillow 则原样返回

    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        return data, mime

    # 1) 超尺寸则等比缩小
    w, h = img.size
    if max(w, h) > _IMG_MAX_SIDE:
        scale = _IMG_MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))

    # 2) 先尝试 PNG（保真）
    fmt = "PNG" if mime in ("image/png", "image/svg+xml") else "JPEG"
    if img.mode in ("RGBA", "P", "LA") and fmt == "JPEG":
        img = img.convert("RGB")

    def _encode(f, q=None):
        b = io.BytesIO()
        if q is None:
            img.save(b, format=f)
        else:
            img.save(b, format=f, quality=q)
        return b.getvalue()

    out = _encode(fmt)
    # 3) 仍超限 -> 转 JPEG 并逐步降质
    if len(out) > _IMG_MAX_BYTES:
        img = img.convert("RGB")
        q = 85
        while q >= 30:
            out = _encode("JPEG", q)
            if len(out) <= _IMG_MAX_BYTES:
                return out, "image/jpeg"
            q -= 15
    return out, ("image/jpeg" if fmt == "JPEG" else mime)


def _file_to_data_url(path: str):
    """读取本地图片 -> (data_url, None)；失败返回 (None, reason)。

    - 用魔术字节嗅探真实 MIME；非通用格式用 Pillow 转码为 PNG；
    - 发送前按 _fit_image_bytes 主动收缩到安全体积。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:  # noqa: BLE001
        return None, f"无法读取文件: {e}"
    if not raw:
        return None, "文件为空"

    mime = _sniff_mime_from_bytes(raw)
    if mime is None:
        return None, "无法识别的图片格式"
    if mime not in _UNIVERSALLY_SUPPORTED_MIMES:
        try:
            raw = _transcode_to_png(raw)
            mime = "image/png"
        except Exception as e:  # noqa: BLE001
            return None, f"格式 {mime} 需转码但失败（缺少 Pillow 或解码器）: {e}"
    try:
        raw, mime = _fit_image_bytes(raw, mime)
    except Exception:
        pass  # 收缩失败也尽量原样发送
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}", None


# --------------------------------------------------------------------------
# 图片输入模式决策
# --------------------------------------------------------------------------
# 视觉能力关键词（用于 aux 模式探测可用的多模态 Provider）：命中模型名即视为可看图
_VISION_KEYWORDS = (
    "gpt-4o", "gpt-4.1", "gpt-4-vision", "gpt-4v", "claude", "qwen-vl", "qwen2-vl",
    "qwen2.5-vl", "qwen2.5vl", "glm-4v", "gemini", "vision", "llava", "moondream",
    "internvl", "minicpm", "yi-vl", "step-1v", "hunyuan-vision", "doubao-vision",
)


def _model_name_looks_vision(mdl: str) -> bool:
    low = (mdl or "").lower()
    return any(k in low for k in _VISION_KEYWORDS)


def find_vision_provider(cfg):
    """在已配置且可用的 Provider 里找一个视觉模型（aux 回退用）。

    返回 (provider_config, model_name)；找不到返回 (None, None)。
    仅在「主模型不支持视觉」时由调用方使用，避免把视觉请求发给无视觉能力的主模型。
    """
    try:
        ordered = cfg.ordered_providers()
    except Exception:  # noqa: BLE001
        ordered = []
    for pc in ordered:
        if not getattr(pc, "enabled", True):
            continue
        # 云端无 Key 必失败，跳过（与 LLMClient._build_chain 口径一致）
        if not pc.api_key and not _is_local_url(pc.base_url):
            continue
        for m in getattr(pc, "models", None) or []:
            if _model_name_looks_vision(m):
                return pc, m
    return None, None


def _is_local_url(base_url: Optional[str]) -> bool:
    u = (base_url or "").lower()
    return any(seg in u for seg in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def decide_image_input_mode(cfg, model_name: Optional[str]) -> str:
    """对标 Hermes ``decide_image_input_mode``，返回 'native' / 'aux'。

    Hermes 依据 model.supports_vision 决策；智枢未逐模型声明视觉能力，
    故以 ``agent.image_input_mode`` 为准：
      - auto / native : 主模型支持视觉，乐观走 native（图片作为 vision content part 传入）；
        若 provider 实际不支持，回退链会报错并提示改配 aux/text。
      - aux / text    : 主模型无视觉，经辅助视觉 Provider 把图片描述成文字注入上下文
        （aux 视觉回退）；无可用视觉 Provider 时仅提示无法分析图片。
    """
    mode = (getattr(getattr(cfg, "agent", None), "image_input_mode", "auto") or "auto").lower()
    if mode in ("text", "aux"):
        return "aux"
    return "native"


def build_aux_vision_prompt(user_text: str, n_images: int) -> str:
    """aux 视觉回退的描述提示（发给辅助视觉模型，让其为非视觉主模型"看图"）。"""
    return (
        f"你是图片描述器。下面有 {n_images} 张图片，请用中文逐张简洁描述它们的"
        f"关键内容（是什么、主体、文字、数字、图表要点等），供一个不支持视觉的"
        f"助手据此回答用户问题。不要猜测不存在的内容。\n"
        f"用户的问题/消息：{(user_text or '')[:400]}\n"
        f"请按『图1：…』的格式逐张输出。"
    )


def format_aux_descriptions(descriptions: list[str]) -> str:
    """把 aux 视觉描述整理为注入主模型的文本块。"""
    if not descriptions:
        return ""
    body = "\n".join(f"- {d}" for d in descriptions if d and d.strip())
    if not body:
        return ""
    return ("[图片内容（由辅助视觉模型描述，供参考）]\n" + body +
            "\n[请基于以上描述回答用户，不要声称自己不能看图。]")


# --------------------------------------------------------------------------
# 内容部件组装
# --------------------------------------------------------------------------
def build_native_content_parts(image_paths: list[str]):
    """对标 Hermes ``build_native_content_parts``。

    把图片以 base64 data URL 作为 ``image_url`` content part 附到用户消息
    （``[Image attached at: <path>]`` 提示文本由调用方用 ``build_image_attach_hint`` 组装）。

    返回 (parts, skipped)：
      - parts : OpenAI 视觉消息里的 content 列表（不含最外层 text 包裹，由调用方组装）；
      - skipped : [(path, reason), ...] 处理失败、需跳过并告知用户的图片。
    """
    parts: list[dict] = []
    skipped: list[tuple[str, str]] = []
    for p in image_paths:
        du, why = _file_to_data_url(p)
        if du is None:
            skipped.append((p, why or "未知原因"))
            continue
        parts.append({"type": "image_url", "image_url": {"url": du}})
    return parts, skipped


def build_image_attach_hint(paths: list[str]) -> str:
    """生成 ``[Image attached at: <path>]`` 提示文本（多张换行）。"""
    if not paths:
        return ""
    return "\n".join(f"[Image attached at: {p}]" for p in paths)


def build_document_context_note(title: str, stored_path: str) -> str:
    """二进制文档上下文提示（对标 Hermes ``_build_document_context_note``）。

    告诉 Agent：文件已落盘在 ``<path>``，请**自己**用 read_file 工具提取文本后再回答，
    不要要求用户粘贴内容。
    """
    return (
        f"[Document attached: '{title}' is saved at: {stored_path}. "
        f"Its text is not inlined here (binary format such as PDF/DOCX/XLSX). "
        f"To read it, extract the document's text yourself via the read_file tool "
        f"before answering, instead of asking the user to paste the contents. "
        f"Read it efficiently in large chunks (e.g. 800-1000 lines each) using "
        f"start_line/end_line (e.g. 1-900, 901-1800, ...), covering the whole "
        f"document, then synthesize your answer in the final step — do not stop "
        f"after only a few pages.]"
    )


# --------------------------------------------------------------------------
# PDF -> 视觉图片（扫描件"看"而非 OCR）
# --------------------------------------------------------------------------
def pdf_has_text_layer(path: str) -> bool:
    """快速探测 PDF 是否存在可提取文本层（文本型 vs 扫描件）。"""
    try:
        import fitz  # 惰性导入（可选依赖）
        doc = fitz.open(path)
        has = False
        for pg in doc:
            t = pg.get_text("text") or ""
            if t.strip():
                has = True
                break
        doc.close()
        return has
    except Exception:  # noqa: BLE001
        return False


def pdf_pages_to_data_urls(path: str, max_pages: int = _PDF_ATTACH_MAX_PAGES,
                           max_bytes: int = _PDF_ATTACH_MAX_BYTES,
                           dpi: int = _PDF_RENDER_DPI):
    """对标 Hermes ``pdf.attach``：**渲染每页为 PNG 作为视觉图片**，让视觉模型直接"读"页面。

    完全不调用任何 OCR 引擎。返回 (pages, meta)：
      - pages : base64 data URL 列表（每页一张，已收缩到安全体积）；
      - meta  : {"pages": 实际渲染页数, "total": PDF 总页数, "skipped": 被上限截断的页数}。

    依赖 fitz（PyMuPDF，环境已装）；缺失或失败返回 ([], {"pages":0,...})，由调用方降级。
    """
    meta = {"pages": 0, "total": 0, "skipped": 0}
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return [], meta
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return [], meta

    total = doc.page_count
    meta["total"] = total
    # 体积 / 页数双重上限（与 Hermes 一致）
    if os.path.getsize(path) > max_bytes:
        max_pages = min(max_pages, 5)
    last = min(max_pages, total)
    meta["skipped"] = max(0, total - last)

    pages: list[str] = []
    try:
        for i in range(last):
            pix = doc[i].get_pixmap(dpi=dpi)
            raw = pix.tobytes("png")
            raw, mime = _fit_image_bytes(raw, "image/png")
            b64 = base64.b64encode(raw).decode("ascii")
            pages.append(f"data:{mime};base64,{b64}")
    except Exception:  # noqa: BLE001
        pass
    finally:
        doc.close()
    meta["pages"] = len(pages)
    return pages, meta


# --------------------------------------------------------------------------
# 路径解析（attachment -> 本地绝对路径）
# --------------------------------------------------------------------------
def resolve_attachment_file(att: dict, data_dir: str, store_dir: str) -> Optional[str]:
    """把附件记录解析为本地可读的绝对路径。

    优先级：stored_path（chat/attach 返回的绝对路径）> url(/media/... 映射到 media 目录)
    > url(被当作本地路径)。都找不到返回 None。
    """
    sp = att.get("stored_path")
    if sp and os.path.isfile(sp):
        return sp
    url = att.get("url") or ""
    if url.startswith("/media/"):
        rel = url[len("/media/"):].lstrip("/")
        p = os.path.join(data_dir, store_dir, rel)
        if os.path.isfile(p):
            return p
    if url and os.path.isfile(url):
        return url
    return None
