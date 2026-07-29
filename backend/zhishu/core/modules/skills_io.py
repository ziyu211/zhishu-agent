"""智枢智能体 —— 技能跨智能体互操作（导入 / 导出）。

目标：让智枢的技能可以「从外部智能体导入」也能「导出给其它智能体」。

导入（import_archive）
---------------------
接收一个压缩包（.zip / .tgz / .tar.gz），自动嗅探并转换多种外部格式为
智枢原生技能目录 ``data/skills/<name>/`` （``SKILL.md`` 正文 + ``module.json`` 元信息）：

  * **Hermes / Claude 系**：``SKILL.md``（带 YAML frontmatter ``name``/``description``/
    ``version``），可能还有 ``references/``、``scripts/``、``templates/`` 子目录。
  * **智枢原生**：``SKILL.md`` + ``module.json``（或旧 ``skill.json``，``content`` 字段）。
  * **通用 Markdown**：任意 ``*.md``（非 README/LICENSE），把 frontmatter 或首段当作描述，
    整体作为指令正文（覆盖 OpenClaw 等以单文件 markdown 描述技能的智能体）。

导出（export_skills）
--------------------
把智枢技能打包为 zip：每个技能一个目录 ``<name>/SKILL.md`` + ``module.json``。
该格式同时是 **Hermes 兼容格式**（Hermes 读 ``SKILL.md``，忽略 ``module.json``），
因此导出后即可被 Hermes / 智枢 / 多数支持 ``SKILL.md`` 的智能体直接识别。

安全：所有解压写盘前做路径穿越检查；技能名经 ``sanitize_name`` 过滤，避免目录穿越。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from typing import Optional

SKILL_MD = "SKILL.md"
MODULE_JSON = "module.json"
SKILL_JSON = "skill.json"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

_SKIP_FILES = {
    "readme.md", "readme", "license", "license.md", "changelog.md",
    "changelog", "package.json", ".ds_store", "manifest.json",
}

# Hermes 分类层常见父目录名（导入时剥掉，仅保留技能目录本身）
_CATEGORY_DIRS = {
    "skills", "skill", "agents", "agent", "commands", "tools",
    "optional-skills", "optional_skills", "skills-lib",
}

# 伴随资源目录：内含 .md 但不应被当作独立技能导入
_COMPANION_DIRS = {
    "references", "scripts", "templates", "assets", "examples",
    "docs", "images", "static", "test", "tests",
}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _load_json(path: str) -> Optional[dict]:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _parse_frontmatter(text: str):
    """返回 (meta_dict, body_without_fm)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    meta: dict = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def _first_line(body: str) -> str:
    for ln in (body or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            return ln[:120]
    return ""


def _slug(name: str) -> str:
    """把任意名称收敛为英文目录名（中文保留，后续 sanitize 会再过滤）。"""
    name = (name or "").strip()
    name = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    return name or "skill"


def _safe_join(base: str, target: str) -> Optional[str]:
    """检查 target 是否落在 base 内，防 zip 路径穿越。"""
    base = os.path.abspath(base)
    full = os.path.abspath(os.path.join(base, target))
    if full == base or full.startswith(base + os.sep):
        return full
    return None


def _extract(archive_bytes: bytes, fmt: str, dest: str) -> None:
    """解压 zip / tar.gz 到 dest（先写临时再校验）。"""
    os.makedirs(dest, exist_ok=True)
    if fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
            for info in z.infolist():
                # 路径穿越检查
                if _safe_join(dest, info.filename) is None:
                    continue
                z.extract(info, dest)
    else:  # tgz / tar.gz
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as t:
            for member in t.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    continue
                try:
                    t.extract(member, dest)
                except Exception:
                    continue


def _collect_skill_dirs(root: str) -> list[str]:
    """找到每个技能自己的目录（剥掉 Hermes 分类层）。

    判定为技能目录的条件（满足其一）：
      * 直接含 SKILL.md / skill.json / module.json；
      * 直接含任意非跳过的 .md（覆盖 OpenClaw 等「skills/<name>.md」单文件风格，
        也覆盖根目录散落的 .md 正文）；
    但「伴随资源目录」（references / scripts / templates …）即使含 .md 也不算技能。
    """
    dirs: list[str] = []
    for cur, _dirnames, files in os.walk(root):
        base = os.path.basename(cur).lower()
        if base in _COMPANION_DIRS:
            continue
        has_skill = any(
            os.path.isfile(os.path.join(cur, f))
            for f in (SKILL_MD, SKILL_JSON, MODULE_JSON)
        )
        has_md = any(
            f.lower().endswith(".md") and f.lower() not in _SKIP_FILES
            for f in files
        )
        if has_skill or has_md:
            dirs.append(cur)
    return dirs


def _detect_skill_from_dir(d: str) -> Optional[tuple]:
    """从一个目录提取 (name, description, version, content, primary_file)。无则 None。

    primary_file 是作为正文来源的文件名（SKILL.md / skill.json / 通用 *.md），
    导入时会跳过它避免重复落盘。
    """
    skill_md = os.path.join(d, SKILL_MD)
    if os.path.isfile(skill_md):
        text = _read(skill_md)
        fm, body = _parse_frontmatter(text)
        name = fm.get("name") or os.path.basename(d)
        desc = fm.get("description") or _first_line(body)
        ver = fm.get("version") or "1.0.0"
        return (_slug(name), desc, ver, body.strip() or text.strip(), SKILL_MD)

    for jf in (MODULE_JSON, SKILL_JSON):
        jp = os.path.join(d, jf)
        if os.path.isfile(jp):
            meta = _load_json(jp) or {}
            content = meta.get("content") or meta.get("instructions") or ""
            if not content:
                continue
            name = meta.get("name") or os.path.basename(d)
            desc = meta.get("description") or ""
            ver = meta.get("version") or "1.0.0"
            return (_slug(name), desc, ver, content.strip(), jf)

    # 通用：目录内任意 .md 作为单文件技能（OpenClaw 等）
    mds = [f for f in sorted(os.listdir(d))
           if f.lower().endswith(".md") and f.lower() not in _SKIP_FILES]
    if mds:
        text = _read(os.path.join(d, mds[0]))
        fm, body = _parse_frontmatter(text)
        if fm.get("type") and "skill" not in (fm.get("type") or "").lower():
            return None
        name = fm.get("name") or os.path.splitext(mds[0])[0]
        desc = fm.get("description") or _first_line(body)
        ver = fm.get("version") or "1.0.0"
        if body.strip():
            return (_slug(name), desc, ver, body.strip(), mds[0])
    return None


def _copy_companions(src_dir: str, dst_dir: str, skip: str | None = None) -> None:
    """复制伴随资源（references/ scripts/ templates/ 等），保留子目录结构。

    skip 为作为正文的源文件名，避免与生成的 SKILL.md 重复落盘。
    """
    for cur, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(cur, src_dir)
        target_base = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        for f in files:
            if f in (SKILL_MD, MODULE_JSON, SKILL_JSON):
                continue
            if skip and f == skip:
                continue
            if f.lower() in _SKIP_FILES:
                continue
            sp = os.path.join(cur, f)
            os.makedirs(target_base, exist_ok=True)
            try:
                shutil.copy2(sp, os.path.join(target_base, f))
            except Exception:
                continue


def import_archive(archive_bytes: bytes, fmt: str, data_dir: str,
                   owner: Optional[str] = None) -> dict:
    """解压、嗅探、转换并写入 data/skills/。返回结构化结果。

    多用户隔离：owner 非空时写入各技能 meta 的 owner 字段（私有归属），
    None/空 = 系统级共享（仅 admin 导入时使用）。"""
    tmp = tempfile.mkdtemp(prefix="skill_import_")
    results: dict = {"imported": [], "skipped": [], "errors": [], "detected_format": []}
    try:
        _extract(archive_bytes, fmt, tmp)
        skill_dirs = _collect_skill_dirs(tmp)
        root = os.path.join(data_dir, "skills")
        os.makedirs(root, exist_ok=True)
        existing = set(os.listdir(root))

        fmt_hit = set()
        for d in skill_dirs:
            parsed = _detect_skill_from_dir(d)
            if not parsed:
                continue
            name, desc, ver, content, primary = parsed
            if os.path.basename(d).lower() in _CATEGORY_DIRS:
                fmt_hit.add("hermes")
            elif os.path.isfile(os.path.join(d, MODULE_JSON)):
                fmt_hit.add("zhishu")
            elif os.path.isfile(os.path.join(d, SKILL_MD)):
                fmt_hit.add("hermes/skillmd")
            else:
                fmt_hit.add("markdown")
            # 去重名称
            final = _uniq_name(name, existing)
            existing.add(final)
            target = os.path.join(root, final)
            os.makedirs(target, exist_ok=True)
            meta = {
                "name": final,
                "description": desc,
                "version": ver,
                "enabled": True,
                "content": content,
                "imported": True,
            }
            if owner:
                meta["owner"] = owner
            try:
                with open(os.path.join(target, MODULE_JSON), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                with open(os.path.join(target, SKILL_MD), "w", encoding="utf-8") as f:
                    f.write(content or desc or "")
                _copy_companions(d, target, skip=primary)
                results["imported"].append({
                    "name": final, "source": name,
                    "description": desc, "version": ver,
                })
            except Exception as e:
                results["errors"].append({"name": final, "error": str(e)})
        # 若没有任何技能目录被识别，尝试把整包根视为一个技能集合后再退一步
        if not results["imported"] and not skill_dirs:
            results["errors"].append({
                "name": None,
                "error": "未在压缩包中识别到任何技能（期望包含 SKILL.md / skill.json / *.md）",
            })
        results["detected_format"] = sorted(fmt_hit)
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _uniq_name(name: str, existing: set) -> str:
    from .runtime import sanitize_name

    base = sanitize_name(name) or "skill"
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def export_skills(data_dir: str, names: Optional[list] = None) -> bytes:
    """打包 data/skills 全部或指定子集为 zip bytes（智枢原生 / Hermes 兼容格式）。"""
    root = os.path.join(data_dir, "skills")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if not os.path.isdir(root):
            return buf.getvalue()
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            if names and name not in names:
                continue
            for cur, _dirs, files in os.walk(d):
                for f in files:
                    fp = os.path.join(cur, f)
                    rel = os.path.relpath(fp, d)
                    z.write(fp, os.path.join(name, rel))
    return buf.getvalue()
