# Hermes 与智枢（Zhishu）对话处理机制对比分析

> 目标：分析 `hermes-agent`（Python 后端）+ `hermes-web-ui`（TS/Vue 前端）处理对话的过程设计与实现，
> 解释「为什么智枢处理起来比 Hermes 慢」，并说明本轮针对智枢的优化与反空转修复。
>
> 代码引用基于 `D:\data\hemers\hermes-agent` 与 `D:\data\hemers\hermes-web-ui`（`hermes-*`），
> 以及 `D:\data\hemers\zhishu-agent`（智枢）截至 1.0.13 + 本轮反空转提交。

---

## 0. 一句话结论

智枢比 Hermes「慢且更容易卡死」的根因不在模型，而在**推理循环的工程实现**：

1. **工具串行执行**：智枢对模型一次返回的多个工具调用 `for tc in tool_calls: await execute(...)`
   严格串行；Hermes 用 8 线程线程池做「分段并行 + 路径冲突分析」。
2. **没有 Prompt 缓存**：智枢每一步都把完整 system + 历史重发给模型；Hermes 有 4 个 `cache_control`
   断点，稳定前缀命中缓存，大幅省 token 与首字延迟。
3. **没有失败熔断**：智枢只有「委派级」去重，对「普通工具反复失败重试」毫无拦截，于是出现
   「16 步 / 23 个工具步骤」的失控循环；Hermes 有 `IterationBudget` + `budget_grace_call` 兜底。

本轮已为智枢补齐三大差距：**叶子工具并发执行**（对标 Hermes 分段并行）、
**三级反空转熔断**（连续失败 / 重复签名循环 / 工具步骤硬上限）、以及**Provider 门控的
Prompt 缓存**（对标 Hermes `prompt_caching.py`，在稳定前缀挂 `cache_control` 断点），
均配套回归测试（提交 `eabf505` 反空转 + `a4538e6` Prompt 缓存）。

---

## 1. Hermes 对话处理流程（agent 侧）

### 1.1 主循环：`agent/conversation_loop.py`

- `run_conversation()` 位于 `conversation_loop.py:1233`。
- 循环条件（`conversation_loop.py:1415`）：
  ```python
  while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) \
        or agent._budget_grace_call:
  ```
  - `max_iterations = 90`（`run_agent.py:446`）—— 远高于智枢 `MAX_STEPS = 16`。
  - `iteration_budget` 是 Hermes 的「迭代预算」对象，配 `budget_grace_call` 做最后宽容一步，
    本质是**步数 + 预算双重护栏**。
- 每一步仍是「全量历史重发」，但配合**上下文压缩**（`context_compressor.py:2254` 50% 阈值）
  与 **prompt caching**，控制每步实际发送量。
- 始终流式（`:2329`）：一旦有 token 立即吐出，不会「工具循环期间长时间无业务事件」。

### 1.2 工具执行并行化：`tool_executor.py` + `tool_dispatch_helpers.py`

- `tool_executor.py:686` 与 `tool_dispatch_helpers.py:116` 实现**分段并行**执行：
  - 用 `DaemonThreadPoolExecutor`，`max_workers = 8`。
  - 先做**路径冲突分析**：`read_file(a)` 与 `write_file(a)` 这种读写同一路径的工具
    归入 `_NEVER_PARALLEL_TOOLS` / 按路径重叠判定，**冲突的工具串行、不冲突的并发**。
  - 这样既快又不破坏「先读后写」类的顺序语义。

> 这正是智枢缺失的部分：智枢 `ToolRegistry.execute` 本身是 `async` / I/O 密集的
> （`registry.py:83 async def execute`），天然可以并发，但 `agent.py` 旧代码却用
> `for tc in tool_calls: await execute(...)` 把它们**串行**了，白白浪费事件循环并发能力。

### 1.3 Prompt 缓存：`agent/prompt_caching.py`

- 在系统提示/上下文上设置 **4 个 `cache_control` 断点**，构造**字节级稳定前缀**
  （身份、工具清单、记忆、知识库固定部分不随轮次变化）。
- 模型服务商（Claude 等）对稳定前缀做缓存命中，后续每步只需发送「变化的尾部」，
  首字延迟与 token 成本显著下降。

> 智枢原无等价机制：系统提示每步重新拼装并整体重发，前缀无法稳定命中缓存。
> 现已在 `providers/prompt_cache.py` 补齐 Provider 门控缓存（见 §4.3）。

---

## 2. Hermes 对话处理流程（gateway + web-ui 侧）

### 2.1 网关：`gateway/platforms/api_server.py`

- 路由（`api_server.py:2021-2025`）：`POST /v1/runs` 触发运行，**立即 `202 Accepted`**
  返回 run id（`:6699`）—— 不阻塞等待首 token。
- 真正的流式走 `GET /v1/runs/{id}/events`：
  - `text/event-stream`，**30s keepalive 心跳**（`:6720`）。
  - 用**内存 `asyncio.Queue`** 中转事件，**不落盘逐 token 写文件**（避免磁盘 IO 卡 SSE）。
- 即「提交即返回 + 独立事件长轮询」双端点结构，天然解耦「请求接入」与「流式推送」。

### 2.2 前端：`hermes-web-ui`

- `packages/client/src/stores/hermes/chat.ts`、`api/hermes/chat.ts`：
  维护会话状态机，订阅 `message.delta`（增量 token）、`tool.started` / `tool.completed`
  等结构化事件。
- `components/hermes/chat/MessageItem.vue`：基于事件**就地渲染**（打字机、工具卡片进度），
  并非每次重渲染整条消息。
- `localStorage` 持久化带 **800ms 节流**，避免高频写造成卡顿。

> 智枢前端是同源 SSE（`agent.run()` 直接 `yield` 事件经 FastAPI StreamingResponse 推送），
> 整体思路一致；本轮未改前端，因瓶颈在后端循环。

---

## 3. 智枢对话处理流程（`core/agent/agent.py`）

- `MAX_STEPS = 16`（`agent.py:52`）。
- 主循环 `for step in range(max_steps):`（`agent.py:647`），每步：
  1. `resp = await self.llm.chat(messages, ...)` 全量重发。
  2. 若 `tool_calls`：`for tc in tool_calls: result = await ToolRegistry.execute(...)` **串行**。
  3. 回填 `tool` 消息 → 下一轮。
- 工具步骤计数 `tool_total` 在多个分支累加（`agent.py:808/874/910/998/1049`）——
  **旧代码没有独立硬上限**，只有 16 步循环上限，于是出现「16 步却跑了 23 个工具步骤」。
- 仅有**委派级**去重（`_delegated_agents` / `_delegate_stall` / `_coverage_nudges`），
  **对普通工具的失败重试循环没有任何拦截**——这就是用户遇到的：
  > `已达到最大推理步数（16），本次已执行 23 个工具步骤：code_exec 装 Node → terminal_run
  > apt-get 被白名单拦截 → code_exec 再下载 → …`

### 3.1 该失控循环的真实根因

- `security.shell_enforce_allowlist` 默认拦截 `apt-get`（`terminal.py:57` 返回
  `[已拦截] 命令 apt-get 不在白名单内`）。
- 模型在「没装成 Node」时反复尝试「code_exec 下载 / terminal_run 安装」，**每次都失败或被拦**，
  而旧代码**既不检测「反复同一失败」也不提前终止**，一路跑到 16 步上限。
- 熔断是**安全网**；该任务真正的正解仍是运维放开白名单或模型自纠错，但熔断确保
  「即便模型不停重试，也不会烧光 16 步还假装正常结束」。

---

## 4. 本轮优化（智枢 1.0.13 + 反空转提交）

### 4.1 叶子工具并发执行（对标 Hermes 分段并行）

`agent.py` `run()` 开头先解析本轮全部 `tool_calls`：

- 若本轮**混有委派**（`delegate_to_agent`）→ 整体串行（委派必须保序且含复杂子流程）。
- 否则当**叶子工具 ≥ 2** 时，用 `asyncio.Semaphore(parallel_tool_workers)` + `asyncio.gather`
  并发执行（I/O 密集，安全），结果按原序收集；**其余副作用（落盘 / 消息回填 / 事件流）
  仍串行原序回放**，保证行为与串行完全等价。
- 新增配置（`config.py` `AgentConfig`）：
  ```python
  parallel_tools: bool = True          # 默认开启
  parallel_tool_workers: int = 5       # 并发上限（信号量）
  ```

> 注：本实现采用「并发执行 + 串行回放副作用」而不是 Hermes 的线程池路径冲突分析，
> 原因是智枢工具执行是 `async` 协程、天然适合事件循环并发，且副作用（消息回填、媒体链接、
> create_team 检测、压缩）必须保序。对典型的「多检索/多读文件」一步多工具场景已能显著提速。

### 4.2 三级反空转熔断（`agent.py` `run()`）

新增状态：`_breaker_sig: dict`（仅对**失败/被拦截**结果累计签名）、`_consec_fail`（连续失败计数）、`_breaker_blocked: dict`（**确定性拦截**按签名累计，阈值更低）、`_blocked_nudged: set`（每签名仅注入一次「勿重试」提醒）。
在每次叶子工具执行后、且 `tool_total += 1` 之后做判定：

| 级别 | 触发条件 | 默认阈值 | 设计意图 |
|------|----------|----------|----------|
| 连续失败 | `_consec_fail >= tool_fail_break` | 6 | 连续 N 次工具失败/被拦即停（如白名单拦截、依赖缺失） |
| 重复签名循环 | 同一 `(工具名, 归一化参数)` 失败累计 `>= tool_cycle_break` | 4 | 捕获「成功/失败交替」死循环（连续计数会被成功清零，故需独立按签名累计） |
| 确定性拦截（早停） | 同一 `(工具名, 归一化参数)` **`[已拦截]`** 累计 `>= min(2, tool_cycle_break)` | 2 | 白名单/`allow_shell`/角色拒绝是确定性必败结果，重复相同调用必再被拦，2 次即停并回显原因，避免浪费额度 |
| 工具步骤硬上限 | `tool_total > max_tool_steps` | 64 | 即便异常步/工具组合也保证终止，防止失控 |

- 失败判定前缀：`[工具错误]` / `[工具执行异常]` / `[已拦截]`（`registry.py:104` 与
  `terminal.py:57` 等）。其中 `[已拦截]` 正是白名单拦截 apt-get 的返回，确保能捕到。
- 终止时 yield 一条**可读的中文提示**而非静默 done，例如：
  > `检测到重复失败循环：工具 terminal_run 以相同参数被反复调用且均失败（累计 4 次），疑似陷入死循环，已自动终止。请检查任务目标或工具配置。`
  > `检测到重复拦截循环：工具 terminal_run 因安全策略拦截（原因：命令 apt-get 不在白名单内……）被反复调用，该拦截为**确定性**结果，重复调用必然再次被拦，已自动终止。请检查工具配置（security.allow_shell / shell_enforce_allowlist / shell_allowlist / 角色权限）或改用被允许的命令，必要时向用户说明该限制。`
- 新配置：
  ```python
  tool_fail_break: int = 6
  tool_cycle_break: int = 4
  max_tool_steps: int = 64
  ```

> 关键设计：签名累计**只对失败结果**进行，成功调用不计入 `_breaker_sig`，
> 避免误伤正常任务中偶发的成功重复调用（如反复 `read_file` 同一文件）。确定性拦截（`[已拦截]`）
> 单独走 `_breaker_blocked` 计数、阈值降至 2 次，并在首次拦截时注入「勿重试」系统提醒，
> 既从源头减少无意义重试，又保留对瞬时失败（`[工具错误]`/`[工具执行异常]`）的 4 次容忍度。

### 4.3 Provider 门控的 Prompt 缓存（对标 Hermes `prompt_caching.py`）

新增 `backend/zhishu/core/providers/prompt_cache.py`，在 `LLMClient._prepare` 完成 `sanitize`
**之后**注入缓存标记（不会被兼容层剥除；注入异常静默兜底，绝不影响主链路）：

- 核心做法：把「稳定前缀（身份 / 指令 / 工具定义）」与「易变内容（检索结果、当前轮输入）」
  用 `cache_control` 断点隔开，使 Provider 的 KV 前缀缓存命中。
- 各家族策略（`prompt_cache` 配置：`off` / `auto` / `force`，**默认 `auto`**）：
  - `anthropic` / `claude`：system 末块 + 末 tool 挂 `cache_control: {type:"ephemeral"}`。
  - `deepseek`：同上 + 置请求级 `prompt_cache=true`。
  - `qwen` / `dashscope` / `aliyun`：走 `extra_body.prompt_cache=true`。
  - `openai` / `azure` / `moonshot` / `kimi` / 未知：依赖服务端**自动前缀缓存**
    （≥1024 tokens 的稳定前缀即命中），**不注入任何标记**以免严格端点 400。
  - 本地 `ollama` / `vllm` / 回环地址：跳过注入（服务端自管 KV 缓存）。
- 关键前提：智枢 `run()` 在**单轮内只构建一次 system 提示词**，且 RAG/长期记忆上下文
  基于同轮恒定的 `user_message`，因此单轮多步推理的前缀天然稳定——缓存收益最大，
  这正是智枢「比 Hermes 慢」的主因之一被消除。

```python
prompt_cache: str = "auto"   # off | auto | force
```

---

## 5. 对比总表

| 维度 | Hermes | 智枢（旧） | 智枢（本轮后） |
|------|--------|------------|----------------|
| 最大步数 | 90（`max_iterations`）+ 预算 | 16（`MAX_STEPS`） | 16（不变） |
| 工具执行 | 分段并行，8 线程池 + 路径冲突分析 | **严格串行** `await` | **叶子工具并发**（信号量，≥2 时） |
| Prompt 缓存 | 4 断点，字节稳定前缀 | 无（每步全量重发） | Provider 门控 cache_control + 服务端自动前缀缓存（**已落地**，见 §4.3） |
| 失败熔断 | `IterationBudget` + `budget_grace_call` | 仅委派级去重 | 连续失败 + 重复签名 + 硬上限（三级） |
| 流式网关 | `POST /v1/runs`(202) + `GET /events`(SSE) | 同源 SSE（`agent.run` 直推） | 同源 SSE（不变） |
| 前端渲染 | `message.delta` 增量 + 800ms 节流持久化 | 同源 SSE 渲染 | 同源 SSE 渲染（不变） |

---

## 6. 后续可选项（非本轮范围）

1. ~~**Prompt 缓存**：在智枢系统提示的稳定段（身份、工具清单、记忆骨架）注入 `cache_control`
   等价标记，使每步只重发变化尾部。~~ **（已落地，详见 §4.3 / 提交 `a4538e6`）**
2. **路径冲突感知并发**：把 Hermes 的「按路径重叠判定串行」移植到智枢
   `asyncio.Semaphore` 调度，进一步避免隐性顺序破坏（当前采用「并发执行 + 串行回放副作用」，
   对多数场景已够用，此项为进阶优化）。
3. **步数预算对齐**：将 `MAX_STEPS=16` 提升为可配（如 32），配合熔断后整体更宽松但不失控。

---

## 7. 验证

- 新增回归测试 `backend/tests/test_antirunaway_breaker.py`（12 断言全绿）：
  - 连续失败熔断（同命令参数略异，专测连续路径）→ 第 6 步熔断。
  - 重复失败循环熔断（code_exec 成功 / terminal_run 被拦 交替）→ 第 7 步熔断。
  - 工具步骤硬上限（全成功但过多）→ 触发终止。
  - 并行叶子工具 + 正常收尾 → 不误触熔断，两工具均执行，最终回答正常。
- 新增回归测试 `backend/tests/test_prompt_cache.py`（22 断言全绿）：
  - Provider 家族识别（anthropic/deepseek/qwen/ollama/vllm/openai/未知/本地回环）。
  - `off` 原样返回；`auto` 下 anthropic 注入 system 末块 + 末 tool 断点、deepseek 加 `prompt_cache`、
    qwen 走 `extra_body.prompt_cache`、openai/未知不注入、本地跳过；`force` 对所有家族注入。
- 既有 `test_agent_media_injection.py` 等保持通过；`config` 默认值校验通过。
