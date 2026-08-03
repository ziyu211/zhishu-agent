"""受控外网访问工具（需安全策略放行）。

包含：
  * safe_web_fetch —— 抓取单个 URL 正文
  * web_search     —— 搜索引擎检索（对标 Hermes web_search_tool，配置驱动多后端）

两者均受 security.outbound_allow 出网闸门约束（在 ToolRegistry.execute 中校验）。
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse

import httpx

from ..base import tool
from ...core.ssrf import guard_url


@tool(
    "safe_web_fetch",
    "抓取指定 URL 的网页正文（仅在安全策略允许出网时可用，默认内网隔离拦截）。",
    {"type": "object", "properties": {
        "url": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["url"]},
    toolset="web",
)
async def safe_web_fetch(args: dict, ctx) -> str:
    # 出网隔离开关在 ToolRegistry.execute 中已校验 security.outbound_allow
    url = args.get("url", "")
    timeout = int(args.get("timeout", 20))
    if not ctx.security or not ctx.security.outbound_allow:
        return "[已拦截] 当前为内网隔离模式，禁止访问外部网络。"
    if not guard_url(url, allow_private=getattr(ctx.security, "allow_private_fetch", False)):
        return ("[已拦截] 目标地址为内网/私有地址，出于 SSRF 防护已拒绝"
                "（如需放开请配置 security.allow_private_fetch=true）。")
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "ZhishuAgent/1.0"})
        return f"[HTTP {r.status_code}]\n{r.text[:4000]}"
    except Exception as e:
        return f"[抓取失败] {e}"


# ---------------------------------------------------------------------------
# web_search —— 配置驱动的搜索引擎检索
# ---------------------------------------------------------------------------

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# DuckDuckGo HTML 结果页解析（零 Key）。结构：<a class="result__a" href="...">标题</a>
# 摘要在 <a class="result__snippet">。href 可能是 /l/?uddg=<真实URL> 跳转链。
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.S)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return _html.unescape(_TAG_RE.sub("", s or "")).strip()


def _ddg_real_url(href: str) -> str:
    """还原 DuckDuckGo /l/?uddg= 跳转链为真实 URL。"""
    if "uddg=" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            real = qs.get("uddg", [""])[0]
            if real:
                return real
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _format_results(items: list[dict], backend: str) -> str:
    if not items:
        return f"[web_search] 无结果（backend={backend}）"
    lines = [f"[web_search] backend={backend}，共 {len(items)} 条："]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.get('title', '')}\n   {it.get('url', '')}")
        snippet = (it.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   {snippet[:300]}")
    lines.append("提示：需要正文时用 safe_web_fetch 抓取具体 URL。")
    return "\n".join(lines)


async def _search_duckduckgo(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": _UA})
    r.raise_for_status()
    text = r.text
    links = _DDG_RESULT_RE.findall(text)
    snippets = [_strip_tags(s) for s in _DDG_SNIPPET_RE.findall(text)]
    out = []
    for idx, (href, title) in enumerate(links[:limit]):
        out.append({
            "title": _strip_tags(title),
            "url": _ddg_real_url(_html.unescape(href)),
            "snippet": snippets[idx] if idx < len(snippets) else "",
        })
    return out


# 中国必应 HTML 结果页解析（零 Key，国内网络可达）。
# 结构：<li class="b_algo"> ... <h2><a href="URL">标题</a></h2> ... <p>摘要</p>
_BINGCN_RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BINGCN_BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BINGCN_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)


async def _search_bing_cn(query: str, limit: int, timeout: int) -> list[dict]:
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url, headers={
            "User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    r.raise_for_status()
    out = []
    for block in _BINGCN_BLOCK_RE.findall(r.text)[:limit]:
        m = _BINGCN_RESULT_RE.search(block)
        if not m:
            continue
        sm = _BINGCN_SNIPPET_RE.search(block)
        out.append({
            "title": _strip_tags(m.group(2)),
            "url": _html.unescape(m.group(1)),
            "snippet": _strip_tags(sm.group(1)) if sm else "",
        })
    return out


async def _search_tavily(query: str, limit: int, timeout: int, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post("https://api.tavily.com/search", json={
            "api_key": api_key, "query": query, "max_results": limit,
        })
    r.raise_for_status()
    data = r.json()
    return [{"title": it.get("title", ""), "url": it.get("url", ""),
             "snippet": it.get("content", "")} for it in (data.get("results") or [])[:limit]]


async def _search_bing(query: str, limit: int, timeout: int, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": limit, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": api_key})
    r.raise_for_status()
    vals = (r.json().get("webPages") or {}).get("value") or []
    return [{"title": it.get("name", ""), "url": it.get("url", ""),
             "snippet": it.get("snippet", "")} for it in vals[:limit]]


@tool(
    "web_search",
    "搜索引擎检索（仅在安全策略允许出网时可用）。输入查询词返回网页搜索结果"
    "（标题/URL/摘要），适合查找最新信息、资料线索。需要网页正文时，"
    "再用 safe_web_fetch 抓取结果中的 URL。后端由 web_search 配置决定"
    "（bing_cn/duckduckgo 零 Key，tavily/bing 需 Key），零 Key 后端失败时自动互备。",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索查询词"},
        "limit": {"type": "integer", "description": "返回条数，默认取配置 max_results"},
    }, "required": ["query"]},
    toolset="web",
)
async def web_search(args: dict, ctx) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "[web_search] 缺少 query 参数"
    from ....context import get_ctx
    wcfg = getattr(get_ctx().cfg, "web_search", None)
    backend = (getattr(wcfg, "backend", "duckduckgo") or "duckduckgo").lower()
    api_key = getattr(wcfg, "api_key", "") or ""
    timeout = int(getattr(wcfg, "timeout", 15) or 15)
    limit = max(1, min(int(args.get("limit") or getattr(wcfg, "max_results", 5) or 5), 10))
    try:
        if backend == "tavily":
            if not api_key:
                return "[web_search] backend=tavily 需要在 web_search.api_key 配置密钥"
            items = await _search_tavily(query, limit, timeout, api_key)
        elif backend == "bing":
            if not api_key:
                return "[web_search] backend=bing 需要在 web_search.api_key 配置密钥"
            items = await _search_bing(query, limit, timeout, api_key)
        elif backend == "duckduckgo":
            # 零 Key 后端互备：duckduckgo 失败（如国内网络不可达）自动改走 bing_cn
            try:
                items = await _search_duckduckgo(query, limit, timeout)
            except Exception:
                backend = "bing_cn(备用)"
                items = await _search_bing_cn(query, limit, timeout)
        else:  # bing_cn（默认，国内网络可达）
            try:
                items = await _search_bing_cn(query, limit, timeout)
            except Exception:
                backend = "duckduckgo(备用)"
                items = await _search_duckduckgo(query, limit, timeout)
        return _format_results(items, backend)
    except Exception as e:
        return f"[web_search 失败] backend={backend}: {type(e).__name__}: {e}"
