"""智枢「学习血缘图谱」（Learning Graph）—— 对标 Hermes ``agent/learning_graph.py``。

聚焦「用户 / 系统随时间真正学到什么」：
  * 技能节点：``data_dir/skills`` 下（排除 ``.archive``），按可见性过滤；
    携带 ``created_by``（agent / user）、``auto_generated``、``created_at``、
    ``use_count``、``source_task``（血缘）、``related``（显式关联）。
  * 记忆节点：用户长期记忆文件 MEMORY.md / USER.md 中的「沉淀」卡片。
  * 边：① 技能间显式 ``related``（两端都存在）；② 记忆↔技能 词面重叠
    （与 Hermes 同思路：lexical overlap）。

纯 stdlib，无 LLM。供后端 ``GET /api/v1/learning-graph`` 返回，前端可渲染。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 元信息 / 记忆读取
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _read_meta(d: str) -> Dict[str, Any]:
    """读 module.json；缺失则尝试 SKILL.md 的 YAML frontmatter 兜底。"""
    fp = os.path.join(d, "module.json")
    if os.path.isfile(fp):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return {}
    md = os.path.join(d, "SKILL.md")
    if os.path.isfile(md):
        try:
            text = open(md, encoding="utf-8").read()
            m = _FRONTMATTER_RE.match(text)
            if m:
                meta: Dict[str, Any] = {}
                for line in m.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
                meta["description"] = meta.get("description", "")
                return meta
        except Exception:
            return {}
    return {}


def _memory_dir(data_dir: str, owner: Optional[str]) -> str:
    """复用 skills.user_memory_dir 的隔离规则（无额外依赖，内联实现）。"""
    if not owner or owner in ("anonymous", "system"):
        return data_dir
    safe = re.sub(r"[^\w.\-]", "_", str(owner))[:64] or "_"
    return os.path.join(data_dir, "memory", safe)


def _memory_cards(data_dir: str, owner: Optional[str]) -> List[Dict[str, Any]]:
    """长期记忆拆成卡片。MEMORY.md 中 maybe_reflect 以 ``---\\n## 沉淀 · <date>``
    分节；USER.md 同样按 ``---`` 分节；每段即一张卡片。"""
    base = _memory_dir(data_dir, owner)
    cards: List[Dict[str, Any]] = []
    for source, fname in (("memory", "MEMORY.md"), ("user", "USER.md")):
        p = os.path.join(base, fname)
        if not os.path.isfile(p):
            continue
        try:
            text = open(p, encoding="utf-8").read().strip()
        except OSError:
            continue
        file_ts = None
        try:
            file_ts = int(os.path.getmtime(p))
        except OSError:
            pass
        # 按 ``## 沉淀`` 或 ``---`` 分节
        chunks = re.split(r"(?m)^#+\s*沉淀\b|^-{3,}$", text)
        for idx, chunk in enumerate(c for c in chunks if c.strip()):
            chunk = chunk.strip()
            if not chunk:
                continue
            first = chunk.splitlines()[0].strip().lstrip("#").strip()
            cards.append({
                "source": source,
                "timestamp": file_ts,
                "title": (first[:80] + "…") if len(first) > 80 else first,
                "body": chunk[:1200],
            })
    return cards


def _tokenize(text: str) -> set:
    return {t for t in re.split(r"[^a-z0-9\u4e00-\u9fff]+", (text or "").lower()) if len(t) >= 2}


def _memory_skill_edges(memory_cards: List[Dict[str, Any]],
                        skills: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    edges: List[Dict[str, str]] = []
    skill_meta = [(s, _tokenize(s["label"]), (s["label"] or "").lower()) for s in skills
                  if s["kind"] == "skill"]
    for idx, card in enumerate(memory_cards):
        mem_id = f"memory:{card['source']}:{idx}"
        text = f"{card.get('title', '')}\n{card.get('body', '')}".lower()
        text_tokens = _tokenize(text)
        scored: List[tuple] = []
        for skill, tokens, name_lower in skill_meta:
            score = 0
            if name_lower and name_lower in text:
                score += 6
            score += len(tokens & text_tokens)
            if score > 0:
                scored.append((score, skill["id"]))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for _, skill_id in scored[:4]:
            edges.append({"source": mem_id, "target": skill_id, "kind": "lexical"})
    return edges


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_learning_graph(data_dir: str, owner: Optional[str] = None,
                         is_admin: bool = False) -> Dict[str, Any]:
    from ..modules.runtime import can_view_meta

    skills_base = os.path.join(data_dir, "skills")
    skill_nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    skill_index: Dict[str, Dict[str, Any]] = {}

    # 1) 技能节点
    if os.path.isdir(skills_base):
        for name in sorted(os.listdir(skills_base)):
            if name.startswith("."):
                continue
            d = os.path.join(skills_base, name)
            if not os.path.isdir(d):
                continue
            meta = _read_meta(d)
            if not meta:
                continue
            if not can_view_meta(meta, owner, is_admin):
                continue
            node = {
                "id": name,
                "label": name,
                "kind": "skill",
                "createdBy": meta.get("created_by", "user"),
                "autoGenerated": bool(meta.get("auto_generated")),
                "createdAt": meta.get("created_at"),
                "state": meta.get("state", "active"),
                "useCount": int(meta.get("use_count") or 0),
                "sourceTask": meta.get("source_task"),
                "related": list(meta.get("related") or []),
                "owner": meta.get("owner"),
                "description": (meta.get("description") or "")[:120],
            }
            skill_nodes.append(node)
            skill_index[name] = node

    # 2) 技能间 related 边
    for node in skill_nodes:
        for rel in node["related"]:
            if rel in skill_index and rel != node["id"]:
                a, b = sorted((node["id"], rel))
                edges.append({"source": a, "target": b, "kind": "related"})

    # 3) 记忆节点
    memory_cards = _memory_cards(data_dir, owner)
    for i, card in enumerate(memory_cards):
        skill_nodes.append({
            "id": f"memory:{card['source']}:{i}",
            "label": card["title"],
            "kind": "memory",
            "memorySource": card["source"],
            "createdAt": card.get("timestamp"),
            "state": "active",
            "useCount": 0,
            "createdBy": "memory",
            "description": card["body"][:200],
        })

    # 4) 记忆↔技能 词面重叠边
    edges.extend(_memory_skill_edges(memory_cards, skill_nodes))

    # 统计
    agent_created = sum(1 for n in skill_nodes
                        if n["kind"] == "skill" and n["createdBy"] == "agent")
    used = sum(1 for n in skill_nodes if n["kind"] == "skill" and n["useCount"] > 0)
    clusters: Dict[str, int] = {}
    for n in skill_nodes:
        clusters[n["kind"]] = clusters.get(n["kind"], 0) + 1

    return {
        "nodes": skill_nodes,
        "edges": edges,
        "clusters": [{"category": c, "count": n}
                     for c, n in sorted(clusters.items(), key=lambda kv: -kv[1])],
        "memory": memory_cards,
        "stats": {
            "skill_nodes": sum(1 for n in skill_nodes if n["kind"] == "skill"),
            "memory_nodes": len(memory_cards),
            "edges": len(edges),
            "agent_created": agent_created,
            "used": used,
        },
    }
