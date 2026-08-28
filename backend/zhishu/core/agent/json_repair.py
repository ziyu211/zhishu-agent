"""工具调用参数的「退化 JSON」修复（缺口②修复核心）。

模型在恢复 create_skill / create_tool 等工具调用时，常生成非法 JSON 参数：
智能/全角引号、字符串内未转义换行、尾随逗号、括号被截断等。若不修复就回放给
Provider 会触发 HTTP 400 掐断对话；若直接置 ``{}`` 则丢光 name/content，工具必失败。

本模块在「丢弃为 {}」之前尝试修复，让工具拿到真实参数正常执行。仅在 ``json.loads``
已失败时才调用，合法 JSON 原样放行、绝不改动，避免破坏复杂参数。
"""
from __future__ import annotations

import json
import re
from typing import Optional


def repair_tool_args(raw: str) -> Optional[str]:
    """尝试修复退化 JSON 工具参数，返回修复后的合法 JSON 字符串；无法修复返回 None。"""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    # 0. 已是合法 JSON —— 原样返回（保险分支）
    try:
        json.loads(s)
        return s
    except (json.JSONDecodeError, TypeError):
        pass

    # 1. 智能 / 全角双引号 -> ASCII "
    s = (s.replace("\u201c", "\"").replace("\u201d", "\"")
           .replace("\u201e", "\"").replace("\u201f", "\"")
           .replace("\u300c", "\"").replace("\u300d", "\""))
    # 2. 全角逗号 / 冒号 -> ASCII
    s = s.replace("\uff0c", ",").replace("\uff1a", ":")
    # 3. 杂散控制字符（BOM、NUL 等，保留 \t）
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff]", "", s)

    # 4. 字符串内未转义的裸换行 -> 转义为 \n
    s = _escape_inner_newlines(s)

    # 5. 尾随逗号：,}" / ,]} / ,} 之前的多余逗号
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    # 6. 括号不闭合：按剩余未闭合数在尾部补 ] / }
    s = _balance_brackets(s)

    # 7. 仍非法则再试「极简兜底」：抽取首个配平 {..} / [..] 片段
    try:
        json.loads(s)
        return s
    except (json.JSONDecodeError, TypeError):
        frag = _extract_balanced_fragment(s)
        if frag is not None:
            return frag
        return None


def _escape_inner_newlines(s: str) -> str:
    """把处于 JSON 字符串字面量内部的裸换行转义为 \\n；字符串外的结构换行直接删去。"""
    out: list[str] = []
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":  # 转义序列：连字符一起保留，跳过下一个
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            i += 1
            continue
        if c == '"':
            in_str = not in_str
            out.append(c)
            i += 1
            continue
        if c in ("\n", "\r"):
            if in_str:
                out.append("\\n")
            # 字符串外的换行直接丢弃
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _balance_brackets(s: str) -> str:
    """补齐被截断参数中未闭合的结构：

      * 先补未闭合的字符串（如 ``"hello`` → ``"hello"``），否则后续按括号配平
        抽到的片段会因字符串缺尾引号而整体非法；
      * 再在尾部补齐未闭合的 [ 与 {（按出现逆序闭合）。
    """
    # 1. 补未闭合字符串：扫描结束后若仍 in_str，则当前字符串缺尾引号
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        i += 1
    if in_str:
        s = s + '"'

    # 2. 补未闭合括号
    opens = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            in_str = not in_str
            i += 1
            continue
        if not in_str:
            if c in opens:
                stack.append(c)
            elif c in ("}", "]"):
                if stack and opens[stack[-1]] == c:
                    stack.pop()
        i += 1
    return s + "".join(opens[ch] for ch in reversed(stack))


def _extract_balanced_fragment(s: str) -> Optional[str]:
    """兜底：从文本中抽取第一个能配平的 {..} 或 [..] 片段并尝试解析。"""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        i = start
        n = len(s)
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = not in_str
                i += 1
                continue
            if not in_str:
                if c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        frag = s[start:i + 1]
                        try:
                            json.loads(frag)
                            return frag
                        except (json.JSONDecodeError, TypeError):
                            break
            i += 1
    return None
