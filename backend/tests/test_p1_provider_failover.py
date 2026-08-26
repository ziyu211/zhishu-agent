"""P1-B 多 Provider 故障转移（超越 Hermes error_classifier）回归测试。

覆盖：
  1. classify_llm_error 对各故障类型的分类 + eager 决策；
  2. 主用 Provider 限流(429)时**立刻**切备用（不烧 5 次重试额度），并产出中文切换提示；
  3. 整条链全失败时抛 AllProvidersFailedError（携带尝试过的 Provider + 中文排查提示）；
  4. 全失败冷却：冷却期内二次调用直接快失败，不再逐 Provider 退避重试（防 agent 卡死）。
"""
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from zhishu.core.config import ZhishuConfig, ProviderConfig
from zhishu.core.providers import client as _client_mod
from zhishu.core.providers.client import LLMClient, AllProvidersFailedError
from zhishu.core.providers.failover import (
    classify_llm_error, RATE_LIMIT, AUTH, SERVER_ERROR,
    TRANSIENT_NET, EMPTY_RESPONSE, MODEL_INVALID,
)

PASS = 0
FAIL = []


def check(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)
        print("  ✗ FAIL:", msg)


def _cfg(providers):
    cfg = ZhishuConfig()
    cfg.providers = {p.name: p for p in providers}
    # resolve_model 把 key 当作 provider 名（或 "provider/model"），故 default_model 用 provider 名
    cfg.default_model = providers[0].name
    return cfg


def _prov(name, models, priority=100, key="k",
          base="https://api.example.com/v1"):
    return ProviderConfig(name=name, label=name, base_url=base, api_key=key,
                          models=models, priority=priority)


async def test_classify():
    print("\n[1] classify_llm_error 故障分类 + eager 决策")
    info = classify_llm_error(RuntimeError("HTTP 429 Too Many Requests"))
    check(info.reason == RATE_LIMIT and info.eager_fallback,
          "429 → 限流 + 立刻切备用")

    info = classify_llm_error(RuntimeError("HTTP 401 unauthorized invalid api key"))
    check(info.reason == AUTH and info.eager_fallback,
          "401 → 鉴权失败 + 立刻切备用")

    info = classify_llm_error(RuntimeError("HTTP 503 Service Unavailable"))
    check(info.reason == SERVER_ERROR and info.eager_fallback,
          "503 → 服务端错误 + 立刻切备用")

    import httpx
    info = classify_llm_error(httpx.ReadTimeout("timed out"))
    check(info.reason == TRANSIENT_NET and not info.eager_fallback,
          "读超时 → 网络抖动 + 不 eager（值得原地重试）")

    info = classify_llm_error(RuntimeError("Provider「x」未返回有效补全：{}"))
    check(info.reason == EMPTY_RESPONSE and info.eager_fallback,
          "空响应 → 立刻切备用")

    info = classify_llm_error(RuntimeError("HTTP 400：The model `foo` does not exist"))
    check(info.reason == MODEL_INVALID and info.eager_fallback,
          "模型不存在 → 立刻切备用")


async def test_eager_switch():
    print("\n[2] 主用 429 → 立刻切备用（不烧重试额度）+ 切换提示")
    cfg = _cfg([_prov("primary", ["m1"]), _prov("backup", ["m2"], priority=200)])
    client = LLMClient(cfg)
    calls = []

    async def fake(pc, model, messages, tools, temperature, max_tokens, tool_choice="auto"):
        calls.append(pc.name)
        if pc.name == "primary":
            raise RuntimeError("HTTP 429 Too Many Requests")
        return {"choices": [{"message": {"content": "ok from " + pc.name}}]}

    client._chat_once = fake
    resp = await client.chat([{"role": "user", "content": "hi"}], model="primary")
    check(resp["choices"][0]["message"]["content"] == "ok from backup",
          "主用 429 后由备用 Provider 返回结果")
    check(calls == ["primary", "backup"],
          f"主用仅尝试 1 次即切（未烧 5 次重试）：实际 {calls}")
    msgs = client.consume_fallback_messages()
    check(any(("已自动切换" in m) and ("backup" in m) for m in msgs),
          f"产出「已自动切换备用」提示：{msgs}")


async def test_all_fail():
    print("\n[3] 整条链全失败 → AllProvidersFailedError（友好提示）")
    _client_mod._CHAIN_EXHAUSTED_AT = 0.0
    cfg = _cfg([_prov("p1", ["m1"]), _prov("p2", ["m2"], priority=200)])
    client = LLMClient(cfg)

    async def fake(pc, model, *a, **k):
        raise RuntimeError("HTTP 429 Too Many Requests")

    client._chat_once = fake
    try:
        await client.chat([{"role": "user", "content": "hi"}])
        check(False, "全失败应抛 AllProvidersFailedError")
    except AllProvidersFailedError as e:
        check(set(e.providers) == {"p1", "p2"},
              f"记录尝试过的 Provider：{e.providers}")
        check("限流" in e.hint, f"限流场景附中文排查提示：{e.hint}")
    except Exception as e:
        check(False, f"应抛 AllProvidersFailedError，实际 {type(e).__name__}: {e}")


async def test_cooldown_fastfail():
    print("\n[4] 全失败冷却：冷却期内二次调用直接快失败（不逐 Provider 退避）")
    _client_mod._CHAIN_EXHAUSTED_AT = 0.0
    cfg = _cfg([_prov("p1", ["m1"]), _prov("p2", ["m2"], priority=200)])
    client = LLMClient(cfg)
    calls = []

    async def fake(pc, model, *a, **k):
        calls.append(pc.name)
        raise RuntimeError("HTTP 429 Too Many Requests")

    client._chat_once = fake
    # 第一次：触发全失败，记录冷却时间戳
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except AllProvidersFailedError:
        pass
    # 立刻第二次（仍在冷却期内）：应直接快失败，每个 Provider 仅试 1 次
    calls.clear()
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except AllProvidersFailedError:
        pass
    check(calls == ["p1", "p2"],
          f"冷却期内不烧重试额度（每个 Provider 仅 1 次）：实际 {calls}")
    _client_mod._CHAIN_EXHAUSTED_AT = 0.0


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_classify())
    loop.run_until_complete(test_eager_switch())
    loop.run_until_complete(test_all_fail())
    loop.run_until_complete(test_cooldown_fastfail())
    print(f"\n=== 通过 {PASS} / 失败 {len(FAIL)} ===")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
