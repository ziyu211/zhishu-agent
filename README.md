# 智枢智能体（Zhishu Agent）

> 一个面向**内网离线、安全合规、自主可控**场景的多用户本地智能体系统。
> 采用 **FastAPI 单进程**同时托管智能体引擎、REST/SSE API 与编译后的前端；配置驱动多模型接入，内置 RBAC 多租户、知识库、记忆、工具、插件/技能/MCP、定时任务与技能自进化闭环。
>
> 最近更新：2026-08-06 — 新增第九节 9.8 国产信创部署、9.9 OpenAI 兼容服务端网关（/v1/chat/completions + /v1/models，复用 RBAC，可对接 Open WebUI / LobeChat）；2026-08-03 完成全量代码审计与架构加固（详见第十一节「安全审计与多用户架构」）。2026-08-08 — 版本统一至 1.0.16；2026-08-09 升级至 1.0.18；新增第八节 8.1「内网 Embedding 模型接入」指南；新增多推理框架兼容画像（vLLM / SGLang / LMDeploy / MindIE / Ollama / Xinference / TGI / llama.cpp / generic）与 Qwen3.5 模板缺陷自愈（去 tools 重试并缓存结论）；**同日工具级 RBAC 下沉**（`terminal_run` / `code_exec` / `create_tool` 开放至 `user`，`operator` 与 `user` 在对话窗口内完全对等）、**`code_exec` 出网与全局 `outbound_allow` 解耦**（新增独立开关 `code_exec_network_isolated`，默认不隔离）、**补齐审计缺口 G1–G6**（令牌吊销/登出、向量长期记忆清理、脱敏统计、统一鉴权守卫与导入缺陷修复，详见第十二节 12.6）。**2026-08-09 升级至 1.0.18**：对全部功能模块做业务闭环复核，修复「登出未清除 /media Cookie 致越权访问他人媒体产物」缺口（前端登出现在调用后端 `POST /api/v1/auth/logout`，吊销令牌并清除 Cookie）。**2026-08-09 升级至 1.0.19**：优化「文件处理→生成→下载链接」闭环——新增 `artifacts.publish_referenced_paths`，自动捕获模型写到 cwd/output 之外（如 `data/generated/attachments/<owner>/...`、`/tmp`、挂载卷）的真实产物并统一补出 `/media` 下载链接；agent 下载护栏兜底覆盖媒体根（零拷贝改写，避免「生成后无下载链接」）；新增可选媒体留存 TTL（`media.retention_days`，默认 0=永久保留，避免自动清理致「文件已清理」）。**2026-08-09 升级至 1.0.20**：修复系统提示词 `_TOOL_GUIDANCE` 仍含「沙箱」字样（与下载护栏「系统提示不再暴露沙箱路径」契约不符），改为「隔离执行环境」措辞，`test_download_guard` 由 32/1 回升至 33/33 全绿。
> **2026-08-09 升级至 1.0.21**：文件处理提速——为 `read_file`（`paths`）、`terminal_run`（`commands`）、`code_exec`（`snippets`）、`generate_excel`（`files`）新增批量参数，把原本 N 次串行工具往返压缩为 1 次调用；`code_exec` 多段合并后在单个子进程内顺序执行（共享解释器与变量，消除每段冷启动一个 Python 的开销）。系统提示新增「减少往返·批量调用（提速关键）」专节引导模型优先合并同类调用，缩小与 Hermes 的体感速度差。
> **2026-08-12 升级至 1.0.22**：补齐「文件处理提速」的框架层短板——此前 `parallel_tools` 虽默认开启，但请求体从未下发 `parallel_tool_calls: true`，模型收不到「可一回合并行发多个工具」的信号，仍逐个串行调用（N 次 LLM 往返 = 比 Hermes 慢的主因）。现于 `compat.sanitize_kwargs` 对支持并行的 Provider 主动下发该参数，配合 agent 循环已有的 `asyncio.gather` 并发执行，把 N 次串行往返压成 1 次；系统提示「减少往返」一节改为强指令（正/反例）。不支持并行的端点经既有自愈回路自动关闭，零回归。新增 `tests/test_parallel_tool_calls.py` 回归。
> **2026-08-12 升级至 1.0.23**：修复 1.0.22 仍漏网的「国产 OpenAI 兼容网关被解析为 generic 后并行信号被剥」问题——`sensenova.cn` / `agnes-ai.cn` 等此前不在云端网关清单，`detect_compat` 返回 `generic`，而 `generic` 的 `drop_params` 含 `parallel_tool_calls` 且默认能力为 False，导致 `parallel_tool_calls: true` 永不下发、模型仍一次一个工具串行（用户实测 trace 仍 8×read_file / 7×code_exec 串行）。现改为：① `CompatProfile.supports_parallel_tool_calls` 默认 `False→True`（乐观下发，不支持的端点经既有自愈回路自动降级）；② `generic` 从 `drop_params` 移除 `parallel_tool_calls`；③ `_CLOUD_HINTS` 补全 `sensenova.cn` / `agnes-ai.cn` 走标准 `openai` 画像。回归测试新增 generic 现下发、sensenova 解析为 openai 且并行=True 两项断言（共 6 项全过）。
> **2026-08-12 升级至 1.0.24**：继续压缩「智枢比 Hermes 慢」的体感差距——把「提速手段」直接钉在模型决策点。诊断结论：框架并行机制（agent.py 的 asyncio.gather）与支持并行的 Provider 信号（parallel_tool_calls: true，1.0.22/1.0.23 已补全）均完好，唯一未解变量是国产网关模型「一次回合只发 1 个 tool_call」的训练范式，导致该信号被无视。故本轮转向攻击模型决策点：① 4 个文件处理工具的顶层 description 全部前置【提速关键】并附批量参数示例（read_file→paths / terminal_run→commands / code_exec→snippets / generate_excel→files），这是最可靠、不依赖模型是否支持并行的提速手段；② read_file / code_exec 在「单数调用」返回时追加 [提速提示] 戳在犯错位置，引导改用批量参数；③ 系统提示「减少往返」一节重写为「批量参数为主、并列发多 tool_call 为 bonus」，对齐国产网关现实。回归测试 tests/test_batch_tools.py 新增 4 项 description 前缀断言 + 2 项单数调用提示断言（全部 ALL_TESTS_PASSED）。
> **2026-08-13 升级至 1.0.25**：继续攻克「智枢比 Hermes 慢」——诊断确认框架并行机制与 Provider 并行信号（1.0.22/1.0.23）均完好，但用户实测 trace 仍 15 次串行单调用（read_file×4 / code_exec×8 / generate_excel×2 / file_write），说明国产网关模型「单回合只发 1 个 tool_call」的硬约束无法靠 parallel_tool_calls 信号扭转。故本轮把杠杆彻底压到「工具列表参数」本身：① 从 4 个工具的 JSON Schema 中**移除标量 path/code/command 参数**（保留 handler 兜底），description 改写为「始终用列表参数」并显式禁止逐文件/逐段调用；② 系统提示新增「先枚举再批量」强制规划规则，要求每种工具只发 1 次调用、把清单全部塞进列表参数；③ 强化 read_file/code_exec 单数调用的 [提速提示] 文案。即便模型仍 1 回合 1 调用，只要把多个目标合并进一次 paths/snippets/commands/files 列表调用，就能把 N 次往返压成 1 次。
> **2026-08-13 升级至 1.0.26**：紧急修复 v1.0.25 引入的「模型参数非法 JSON 回放 400」致命缺陷（agnes1 等 OpenAI 兼容网关校验 assistant tool_call.arguments，破损参数会导致整轮对话 HTTP 400 掐断，报「所有 LLM Provider 均不可用」）。根因：agent 循环在 json.loads 失败时静默吃掉异常、却把破损 arguments 原样回放；v1.0.25 强制 snippets 列表（多段含引号/换行的代码）更易诱发模型产出非法 JSON，且使 code_exec 调用从 ×8 飙升到 ×18。修复：① agent.py 解析 tool_calls 时若 arguments 非法 JSON，就地替换为合法 `{}` 并注入「请以合法 JSON 重试」系统提醒（不再以 400 掐断）；② 回退 v1.0.25 强制列表参数，恢复 v1.0.24「标量主用 + 列表可选」可靠基线（code_exec 改推「把整个任务写成一个完整脚本一次跑完」以减少调用次数）。

> **2026-08-13 升级至 1.0.27**：继续压缩「智枢比 Hermes 慢」的体感差距——针对用户实测 19 次串行调用（read_file×4 / code_exec×13 / generate_excel×2）的路径。诊断：框架并行（asyncio.gather）、Provider 并行信号、批量参数、以及 v1.0.24 决策点 description 均已就位，但 qwen3.5/agnes1 等国产网关模型仍「单回合 1 个 tool_call + REPL 式增量」（连发 13 次不同代码的 code_exec 调试），既有 `_breaker_repeat` 仅拦截「相同参数」循环、对「不同参数但同工具反复调用」无效。本轮新增**同工具多次调用「合并提醒」**（agent.py `run()` 内，v1.0.27）：按工具名累计本轮调用次数（不同参数也算），达阈值（code_exec/read_file/terminal_run=3，generate_excel=2）即注入一次性系统提醒，引导把剩余工作合并为更少/批量调用。该机制**完全不改 JSON Schema**，与既有 `_fail_nudged`/`_repeat_nudged` 同源、零回归风险；并给 code_exec description 补「严禁 REPL 式逐步调试」反模式。注意：框架侧杠杆已基本用尽，更深提速取决于推理服务侧的工具调用微调（更好的 tool-parser / chat-template）。

> **2026-08-13 升级至 1.0.29**：修复「生成 Excel 失败」的新故障——用户实测 generate_excel 只拿到 filename、拿不到数据，直接报错「缺少表格数据」。根因是工具链断裂：模型在 code_exec 里把表格只 print 成文本、没落盘成文件也没内联传给 generate_excel，数据卡在对话文本里。本轮给 generate_excel 增加 **from_file 桥接**（v1.0.29）：① 支持读取 code_exec 写出的 CSV/JSON 文件，入参可为绝对路径 / `/media` 链接 / 沙箱相对文件名（推荐：code_exec 写完 CSV 自动发布得到 /media 链接，再把它作为 from_file 传入即可生成合法 xlsx）；② 仅传 filename 时自动采用本用户沙箱最近生成的表格文件兜底；③ 仍无数据时返回带 from_file 指引的可操作报错。同步在 code_exec / generate_excel 的 description 把「提取→落盘文件→桥接」工作流钉死。回归测试 tests/test_batch_tools.py 新增 check_generate_excel_from_file（from_file 链接 / 沙箱兜底 / 报错 / JSON 表头）。

> **2026-08-13 升级至 1.0.29**：针对「REPL 式增量循环」（用户实测 46 次串行调用 = read_file×12 / code_exec×37）的路径。诊断：v1.0.27 的「同工具合并提醒」仅注入一次（`_consolidate_nudged` 去重），对连发数十次 code_exec 的逐步调试循环完全无效——模型无视一次性提醒一直循环。本轮升级为 **code_exec 硬熔断**（v1.0.29，agent.py `run()`）：`code_exec` 超过阈值（8 次）后进入「熔断-放行」交替模式——奇数次调用（放行）执行并追加「最后一次机会」强指令，偶数次调用（熔断）不执行代码、强制整合为单脚本 + 用 generate_excel from_file 产出交付物。前 8 次完全自由（覆盖合法复杂任务），之后每隔一次强加摩擦打断 REPL 节奏，且交替放行保证任务终能推进、不会死锁。与既有反空转护栏一致，绝不整体终止任务、零回归（不改 JSON Schema）。回归测试新增 check_code_exec_breaker（超阈值交替状态机 / 文案关键要素）。

> **2026-08-13 升级至 1.0.30**：针对「read_file 逐文件读取」新瓶颈（用户实测 26 次串行调用 = read_file×13 / code_exec×11 / generate_excel×1）。诊断：v1.0.29 的 code_exec 硬熔断已把 code_exec 从 37 次压到 11 次（REPL 循环被打断），但 read_file 反而暴露成新主因——模型明知 file_list 已给出文件清单，仍逐个单文件 read_file（13 次）。`read_file` 早已支持 `paths` 列表批量读取、description 也强提示，但软提示对国产网关模型无效（同 v1.0.29 前的 code_exec）。本轮把同一套**硬熔断**思路迁移到 read_file（v1.0.30，agent.py `run()`）：单文件读取超过阈值（6 次）后进入「熔断-放行」交替——偶数次单读被拦截（不读取、强令改用 `paths` 列表），奇数次单读放行但追加「改用 paths 批量」强指令；**`paths` 批量读取（>=2 文件）始终放行**，既奖励正确行为、又保证模型永远有出路、不会死锁。不改 JSON Schema、零回归。回归测试新增 check_read_file_breaker（is_batch 真值表 / 超阈值交替状态机 / 批量始终放行）。

> **2026-08-14 升级至 1.0.31**：严格代码审查 + 全链路业务闭环审计（C1–C15 回归脚本 `backend/tests/test_closure_audit.py` 96 项、`http_closure_check.py` 24 项、`test_batch_tools.py` 全绿）+ 修复「创建技能报错」。诊断：用户要求创建 WDPB 技能时，agent 误在 `code_exec` 沙箱脚本里调用对话层工具 `create_tool` 而报错——根因是 `code_exec` 是独立 Python 子进程，沙箱内无 `ToolRegistry`/`create_tool`/`ctx`，进程隔离层面不可能（设计如此，非 bug）；`create_tool` 在对话层经 `ToolRegistry.execute` 闸门调用、注册 `dyn_` 工具后下一轮即对 LLM 可见可调用（已实测跑通）。本轮在 `code_exec` 的 description 补明确护栏：「脚本内只能写纯 Python，不能调用 create_tool 等其他工具，它们由对话层 tool_call 直接调用」；并明确正确路径——可复用工具直接发 `create_tool` tool_call，一次性文档处理直接 `code_exec` 跑 python-docx。不改 JSON Schema、零回归。

> **2026-08-14 升级至 1.0.32**：全面补强「文档与图片处理」能力（用户反馈 .doc 无法解析）。诊断：① 旧版 OLE 二进制 **.doc/.xls/.ppt 此前完全无法结构化解析**（.doc/.xls 仅「尽力而为」字节扫描、常提为空；**.ppt 连分支都没有，直接掉通用报错**），且 OLE 嗅探器会把 .ppt 误判成 .doc；② 图片与扫描 PDF **全仓无 OCR**，图片只当视觉参考、扫描 PDF 只渲染成图。本轮在双 Dockerfile 注入 LibreOffice 无头转换（writer/calc/impress）+ 中文字体（fonts-noto-cjk）+ Tesseract 中文 OCR（chi_sim+eng），`requirements.txt` 加 `pytesseract`；`rag.py` 新增 `_libreoffice_convert`（全局 threading 锁串行化 soffice，转换后递归走既有提取器）把 .doc/.xls/.ppt 转新格式解析，新增 `_ocr_image_bytes`/`_ocr_pdf` 为图片与扫描 PDF 自动 OCR（无引擎则优雅降级）；修正 OLE 嗅探器区分 .ppt。覆盖旧格式文档「零用户操作」自动解析、图片/扫描件中文提取，与已有零依赖解析器互补、不重复造轮子。

---

## 一、设计目标

- **单进程部署**：一个 FastAPI 进程同时承担「Agent 引擎 + API 服务 + 静态前端托管」，无需独立的 Node BFF 或反向代理，镜像体积小、部署单元合一。
- **配置驱动的多模型接入**：LLM / Embedding 调用跟随 `deploy/zhishu.yaml` 与运行时 Provider 管理（`data/providers.json`），支持国产 OpenAI 兼容端点（通义千问、智谱 GLM、DeepSeek、Kimi、文心一言、MiniMax、讯飞星火）以及本地 Ollama / vLLM。
- **多用户与租户隔离**：内置四角色 RBAC（admin/operator/user/viewer），资源默认私有，支持「私有 / 全员共享 / 按角色共享」三级共享粒度，admin 可代管任意用户。
- **安全合规**：数据脱敏、审计日志、国密（SM4/SM3）凭据保护、出网隔离、SSRF 防护、媒体文件鉴权网关、弱密钥启动硬闸门。
- **可离线运行**：默认使用本地 SQLite 向量库与确定性哈希向量降级，无外部服务依赖即可启动。

---

## 二、整体架构

```
浏览器 ──(同源 fetch / SSE)──▶ Python(FastAPI 单进程)
                                  │
                                  ├── 智能体引擎（ReAct 循环 + 流式 SSE）
                                  ├── REST / SSE API（RBAC + 多租户隔离）
                                  ├── /media 鉴权网关（按 owner 隔离）
                                  └── 编译后的 Vue 静态前端（static/）
```

由 **FastAPI 单进程**统一托管：① Agent 推理引擎 ② REST/SSE API ③ 编译后的 Vue 静态资源。前端通过同源调用后端，开发期与生产期共用同一套契约，无 Node BFF。

---

## 三、RBAC 权限模型与多租户

### 3.1 角色权限矩阵（`core/security.py` ROLES）

| 权限点 | admin | operator（运维/配置） | user（普通用户） | viewer（只读访客） |
|--------|:-----:|:--------:|:----:|:------:|
| chat（对话） | ✅ | ✅ | ✅ | ✅ |
| knowledge:read / write | ✅ | ✅ / ✅ | ✅ / ✅ | ❌ |
| models:read / write | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| modules:read / write（插件/技能/MCP/记忆） | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| agents:read / write | ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| cron:read / write | ✅ | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ |
| audit:read（审计） | ✅ | ✅ | ❌ | ❌ |
| users / settings / admin 端点 | ✅ | ❌ | ❌ | ❌ |

- `admin` 拥有通配 `*`；写权限隐含读权限；前端以 `app.can('<perm>')` 统一门控按钮/表单，无权限时展示「只读」徽标。
- **cron shell 动作**额外限制：仅 admin/operator 可创建/修改 `action=shell` 的定时任务（防任意命令执行）。

### 3.2 多租户隔离与共享

- 所有资源（Provider / 插件 / 技能 / MCP / Agents / 定时任务 / 知识文档 / 会话 / 媒体文件）带 `owner`，**默认私有**。
- 共享粒度三态（前端 `ShareScopeSelector`）：**私有** → **按角色共享**（`share_with: ["operator","user","viewer"]`）→ **全员共享**（`shared: true`）。
- `owner` 为空的资源视为「**公共**」：全员可见、**仅 admin 可改/删**。
- admin 可通过 `X-Act-As` 请求头（前端「切换用户」）代管任意用户的资源。
- 媒体文件按 `/media/<owner>/<file>` 存储，网关校验 Token 归属，禁止跨租户访问。

### 3.3 工具级 RBAC（执行闸 / 可见性闸 / 内部闸）

除 3.1 的权限点矩阵外，高危工具另有**工具级角色闸门** `core/tools/registry.py` 的 `TOOL_MIN_ROLE`，与权限点相互独立、双保险：

| 工具 | 最低角色 | 说明 |
|------|----------|------|
| `terminal_run` | `user` | 沙箱终端命令执行 |
| `code_exec` | `user` | 沙箱代码执行（与 `terminal_run` 同源 Python 引擎） |
| `create_tool` | `user` | 运行时自建工具（同源执行引擎，随 `code_exec` 一并下放） |
| 其余内置工具 | —（无限制） | 四角色均可用 |

- **三道闸**：
  1. **执行闸**（`registry.execute`）：`user_role` 低于 `TOOL_MIN_ROLE` 直接返回 `[已拦截] 当前角色无权使用该工具`，fail-closed。
  2. **可见性闸**（`runtime.tool_visible_to`）：低于最低角色的工具不在前端工具清单暴露。
  3. **内部闸**：`code_exec._code_exec_allowed` / `terminal.py` 内部仍有角色判定兜底。
- **当前角色对等性**：`operator` 与 `user` 在默认对话窗口内对文档处理 / 工具调用**完全对等**（`terminal_run` / `code_exec` / `create_tool` 三者均 `user`，无差异）；`admin` 为严格超集（X-Act-As 代管、跨用户读媒体、可见他人私有模块）。
- **`viewer` 双闸 fail-closed**：既不可见也不可执行上述高危工具。
- **`cron` 的 `shell` 动作**：仍仅 `admin` / `operator` 可创建/修改（命令再由 `core/shellguard.py` 纵深防御闸门校验），不受工具级下沉影响。

---

## 四、核心模块

### 1. LLM 接入层（配置驱动多 Provider + 回退链）
- **抽象 `LLMProvider`**：基于 `httpx` 自研轻量客户端，零境外 SDK 依赖（`core/providers/`，`core/llm.py` 为兼容 shim）。
- **多 Provider**：国产 OpenAI 兼容端点 + 本地推理（Ollama / vLLM）。
- **运行时 Provider 管理**：前端「模型」页支持增删改 Provider、探测模型（`/models/fetch`，带 SSRF 防护）、设默认模型、按共享范围下发；持久化至 `data/providers.json`（API Key 以 SM4/XOR 混淆落盘，接口只回掩码）。
- **故障回退链 + 负载均衡**：按可用性与密钥状态自动筛选，主用不可用时回退备用。
- **多推理框架兼容**：内置 vLLM / SGLang / LMDeploy / MindIE / Ollama / Xinference / TGI / llama.cpp / generic 兼容画像（`compat`），按 `base_url`/端口自动探测或显式声明，规避各框架对 system 位置、content:null、function calling、未知参数的差异；4xx/5xx 触发自愈（历史裁剪、去 tools 重试等，如 Qwen3.5 模板缺陷导致的 `No user query found in messages.` 会自动去 tools 重试并缓存结论）。
- **离线兜底**：未配置云端 Key 时可指向本地 Ollama。

### 2. 知识库与 RAG
- **向量库**：默认 `SQLite + numpy 余弦相似度`，零外部服务内网直用；`vector_store.py` 预留 Milvus / pgvector / 达梦(DM) 后端接口。
- **Embedding**：网络 Provider 端点或本地模型，批量入库；未配置时降级确定性哈希向量，检索流程不中断。
- **文档解析**：零依赖解析 PDF/Office 等（`parsers.py`），扫描件 PDF 支持页面图片预览；文档按 owner 隔离、可共享，删除共享文档仅 admin。
- **知识图谱**（`kgraph.py`）：从入库文档抽取实体关系，前端图谱可视化。

### 3. 记忆系统
- **会话记忆**：SQLite FTS5 存储多轮对话，跨会话全文检索。
- **长期记忆工具**（`memory`）：Agent 主动 `recall / save / update_user / forget`，持久化 `MEMORY.md / USER.md / SOUL.md`，下轮注入系统提示；设置页可开关（admin，持久化 `data/config.override.json`）。
- **跨会话回忆**（`session_search`）：关键词检索 / 指定会话翻阅 / 最近会话浏览，零 LLM 成本。

### 4. 工具系统（Toolset）
工具按能力分组（`toolsets`），按需启用：

| 工具分组 | 工具 | 说明 |
|----------|------|------|
| `terminal` | `terminal_run` | 沙箱内终端命令执行 |
| `file` | `file_read` / `file_write` / `file_list` | 文件读写（大文件分页 + 零依赖文档解析） |
| `knowledge` | `knowledge_search` / `knowledge_list` / `knowledge_read` | 知识库检索 |
| `web` | `safe_web_fetch` / `web_search` | 受控外网访问（`outbound_allow` 门控）；多搜索引擎后端 |
| `memory` | `memory` | 长期记忆读写 |
| `todo` | `todo` | 任务清单 |
| `sessions` | `session_search` | 跨会话回忆 |
| `delegate` | `delegate_to_agent` | 委派子代理 |
| `skills` | `read_skill` | 技能渐进披露 |
| `code_exec` | `code_exec` | 沙箱内代码执行 |

- **沙箱**：工具仅能在 `SANDBOX_ROOT`（默认 `data/sandbox`）内操作。
- **出网隔离开关**：`safe_web_fetch` / `web_search` 受 `security.outbound_allow` 门控（默认 `false` = 内网隔离）。`code_exec` 的执行**与全局 `outbound_allow` 解耦**，由独立开关 `security.code_exec_network_isolated`（默认 `false` = 不隔离、允许出网）单独管控——内网抓数据 / 跑脚本默认即可联网，真正硬隔离仍靠基础设施防火墙 / 容器 egress。
- **多模态生成**：支持图片生成/视频生成路由（`image_routing.py`），产物经 `MediaStore` 按 owner 落盘、`/media` 网关鉴权访问。

### 5. 插件 / 技能 / MCP（运行时模块）
- **插件（Plugins）**：上传/编写 Python 插件即注册为工具，增删改/启停即时生效（`modules.py _sync_plugins`），支持共享范围。
- **技能（Skills）**：SKILL.md 技能包管理，导入/新建/编辑，Agent 按需渐进读取。
- **MCP 服务器**：配置 stdio MCP Server（command/args/env），连接后其工具注册为 `mcp__<server>__<tool>`，前端支持连接测试与工具调用测试。
- 三者均按 owner 隔离 + 三态共享，非 owner 且非 admin 只读。

### 6. 安全合规模块
- **数据脱敏**（`redact.py`）：手机号/身份证/邮箱/银行卡等正则遮蔽，审计落库前自动脱敏；系统页提供「脱敏自测」卡片（`/admin/redact`）。
- **国密与凭据保护**（`credentials.py` / `security.py`）：API Key 落盘 SM4 混淆（库缺失降级 XOR）；Token HMAC，可选 SM3；密码加盐哈希。
- **启动硬闸门**：`enable_auth=true` 且 `security.secret` 为默认值时**拒绝启动**（可用 `ZHISHU_ALLOW_INSECURE_DEFAULTS=1` 临时放行）；弱管理员口令启动告警。
- **SSRF 防护**：`/models/fetch` 默认拒绝内网/私有/环回地址（含云 metadata），需 `security.allow_private_fetch: true` 显式放开（本地 Ollama 探测场景）。
- **审计日志**：关键操作记录，脱敏后落库，operator 以上可查。
- **CORS**：`allow_credentials=False`，避免 `*` + credentials 危险组合。
- **上传防护**：知识库与对话附件接口改用 `core/upload.read_upload_limited` 分块读取，超过 100MB 返回 413，防止大文件打满内存/磁盘。
- **SSRF 扩面**：除 `/models/fetch` 外，`safe_web_fetch` 与插件 http 类工具统一经 `core/ssrf.guard_url` 拦截内网/私有/回环/云元数据地址。
- **SPA 静态兜底修复路径穿越**：`main.py` 的 SPA fallback 改用 `realpath` 归一化并校验目标仍位于 static 目录内，杜绝 `../` 越权读取 `data/providers.json` 等敏感文件。
- **前端存储型 XSS 修复**：`MarkdownRenderer.vue` 表格单元格统一走 `inline()` 转义后再 `v-html` 渲染，避免 Markdown 注入持久化跨会话执行。
- **脱敏 fail-closed**：`redact` / `redact_dict` 在异常时整体隐藏并记录日志，而非回退原文，避免 PII 泄露。

### 7. 上下文压缩
`context_engine.py` 超长对话自动截断/摘要/轮转，保证长会话可用。

### 8. 定时任务（Cron）
`cron.py` 内网合规调度器：`interval / daily / cron` 三种调度，`chat`（定时对话，按任务 owner 的身份与角色执行）与 `shell`（仅 admin/operator）两种动作；任务按 owner 隔离，viewer 只读。

### 9. 技能自进化闭环
- **技能蒸馏**（`modules/skills.maybe_learn`）：复杂任务完成后异步沉淀 SKILL.md。
- **后台记忆反思**（`maybe_reflect`）：每轮结束异步蒸馏用户事实（opt-in 默认关闭）。
- **会话内 nudge**：按配置频率提醒模型沉淀记忆/技能。

### 10. 多智能体协作
- **子代理委派**（`delegate_to_agent`）+ **自定义 Agents**（前端可建专属 Agent：提示词/模型/工具集，支持共享）。
- **MoA 多模型协作**（`core/agent/moa.py`）。
- **委派路由（服务端分类器权威裁决，默认单智能体）**：主管（超级用户/默认会话）收到消息后由
  `Agent._needs_supervisor_delegation()` 分类，**默认不委派、直接作答（路径 A）**，仅当出现以下
  **显式**信号才进入多 Agent 协作（路径 B）：
  1. 显式要求组建/创建团队，或显式委派给某协调者（如 `用Orchestrator`、`让投资总监`）；
  2. **显式点名某团队**（如「股票分析团队」「业务分析团队」「用股票分析团队分析…」）；
  3. 显式要求「多智能体协作 / 多模型并行」（`多智能体`、`多agent`、`moa模式`）。
  > 注意：消息中**顺带提及领域词**（如「除了股票分析你还能做什么」「帮我分析下这段代码」）**不会**触发
  > 多 Agent 协作——避免弱模型把普通问题丢进团队协作浪费资源。路径 A 下 `delegate_to_agent`/`create_team`
  > 会被**物理剥离**，模型无法自行越权委派。

### 11. 推理循环优化（对标 Hermes）

为消除「智枢比 Hermes 慢且易卡死」的工程差距，推理主循环（`core/agent/agent.py` `run()`）补齐三项优化（完整对比见 `docs/hermes_vs_zhishu_dialogue.md`）：

- **叶子工具并发执行**：同一 LLM 响应返回 ≥2 个「非委派」叶子工具时，经 `asyncio.Semaphore` + `gather` 并发执行（I/O 密集，安全），副作用仍串行原序回放，行为等价于串行；委派类工具始终串行保序。
- **三级反空转熔断**：
  - 连续失败：连续 `tool_fail_break`（默认 6）次工具调用均失败/被拦截即终止；
  - 重复签名循环：同一 `(工具名, 归一化参数)` 失败累计 `tool_cycle_break`（默认 4）次即终止，兜底「成功/失败交替」式死循环（如 `code_exec` 装依赖成功、`terminal_run` 装依赖被白名单拦截反复出现）；
    - **确定性拦截优先（Task #399）**：`[已拦截]` 为安全策略（白名单 / `allow_shell` / 角色）决定的必败结果，重复相同调用必然再次被拦，故仅 **2 次**即提前终止并回显拦截原因（白名单/开关/角色），避免把额度浪费在注定失败的调用上；同时在首次拦截时向模型注入「勿重试」系统提醒，引导其改用其他命令/工具。
  - 工具步骤硬上限：`max_tool_steps`（默认 64）独立于 16 步循环上限，保证终止。
  - **重复成功循环熔断（Task #399）**：原熔断仅统计**失败**，故「反复 `read_file` 同一文档 / 反复调用同一只读工具」这类**全成功**停滞（连续失败计数为 0）会一路烧到 `max_tool_steps` 才停。新增 `_breaker_repeat` 按完整签名 `(工具名, 归一化参数)` 累计**成功**重复调用，达 `tool_repeat_break`（默认 8）即终止并提示「改用以增量处理（分页/检索）替代整篇重读」。阈值远低于 64，使失控在 8 次内即止；读取不同文件 / 不同行范围签名不同，不会误触发。
  - 失败判定含 `[已拦截]`（白名单拦截 apt-get 等），终止时给出可读中文提示而非静默结束。
- **Provider 门控的 Prompt 缓存**：在 `LLMClient._prepare` 完成 sanitize 后注入缓存标记，使 Provider KV 前缀缓存命中，缩短多步推理的重复前缀耗时：
  - `anthropic`/`claude`：system 末块 + 末 tool 挂 `cache_control`；
  - `deepseek`：同上 + 请求级 `prompt_cache=true`；
  - `qwen`/`dashscope`：走 `extra_body.prompt_cache=true`；
  - `openai`/`azure`/未知：依赖服务端自动前缀缓存（≥1024 tokens 稳定前缀即命中），不注入标记以免严格端点 400；
  - 本地 `ollama`/`vllm`/回环地址：跳过注入。
  - 配置 `prompt_cache`：`off` / `auto`（默认） / `force`。

---

## 五、目录结构

```
zhishu-agent/
├── README.md                 # 本文档
├── requirements.txt          # 依赖清单（国内 pip 源可用）
├── backend/
│   ├── start_backend.py      # 启动入口（uvicorn 托管 create_app，默认 0.0.0.0:8080）
│   ├── tests/                # 单测与端到端脚本（含 e2e_roles_check.py 四角色验证）
│   └── zhishu/
│       ├── main.py           # FastAPI 应用工厂（引擎 + API + /media 网关 + 静态托管 + 启动自检）
│       ├── core/
│       │   ├── config.py     # YAML + 环境变量 + config.override.json 配置中心
│       │   ├── providers/    # Provider 客户端与回退链（llm.py 为兼容 shim）
│       │   ├── credentials.py# Provider 运行时存储（SM4/XOR 混淆，providers.json）
│       │   ├── embedding.py  # Embedding（网络 / 本地 / 哈希降级，批量）
│       │   ├── vector_store.py # 向量库（sqlite 实现 + 多后端接口）
│       │   ├── rag.py        # 知识库检索增强 + 扫描件预览
│       │   ├── kgraph.py     # 知识图谱抽取
│       │   ├── memory/       # 会话记忆（SQLite FTS5）
│       │   ├── modules/      # 插件/技能/MCP 运行时 + 自进化模块
│       │   ├── agent/        # Agent 循环（ReAct + SSE）+ moa.py
│       │   ├── agents_runtime.py # 子代理运行时
│       │   ├── security.py   # 鉴权 / RBAC(ROLES) / 用户库 / 审计
│       │   ├── redact.py     # 数据脱敏
│       │   ├── media.py      # 媒体存储（按 owner 目录隔离）
│       │   ├── cron.py       # 定时任务调度
│       │   ├── image_routing.py # 多模态生成路由
│       │   └── tools/        # 工具注册中心 + builtins/
│       ├── api/              # auth/chat/conversations/knowledge/models/modules/
│       │                     # agents/cron/users/settings/admin 共 12 路由
│       └── static/           # 编译后的前端资源（随仓库分发，构建后覆盖）
├── frontend/                 # Vue3 + Vite + Naive UI（同源调用后端）
│   └── src/ ...
└── deploy/
    ├── zhishu.yaml.example   # 示例配置（复制为 zhishu.yaml 后填密钥，真实配置不入库）
    ├── Dockerfile            # 标准容器化构建（node:18-alpine + python:3.11-slim）
    ├── Dockerfile.local      # 受限/离线网络专用（华为云 SWR 基础镜像 + 国内源）
    └── docker-compose.yml    # 一键编排（后端 + 可选本地 Ollama）
```

> 注：`backend/zhishu/static/` **随仓库分发**（便于离线部署直接运行）；`deploy/zhishu.yaml` 真实配置已被 .gitignore 忽略。

---

## 六、核心数据流

```
用户提问 → API(/api/v1/chat, SSE, RBAC + X-Act-As)
   → Agent 循环：
       1. 组装上下文：会话记忆(memory) + 知识库(rag) + 长期记忆(MEMORY.md)
       2. 调用 LLM（按用户可见 Provider 解析回退链）
       3. 解析 tool_calls → 工具注册中心执行（沙箱 / 审计 / 出网门控）
       4. 结果回填，循环直至 final_answer；媒体产物按 owner 落盘
   → 流式返回 token / tool_call / done 事件
   → 异步触发：技能蒸馏(maybe_learn) 与（可选）记忆反思(maybe_reflect)
```

---

## 七、快速开始

### 7.1 部署前准备

```bash
# 1) 复制配置模板（deploy/zhishu.yaml 已被 .gitignore 忽略，请勿提交真实密钥）
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 编辑 deploy/zhishu.yaml：填入 Provider api_key、管理员密码，
# 并将 security.secret 改为强随机值（默认值会触发启动硬闸门，进程拒绝启动）

# 2) 安装后端依赖
cd backend && pip install -r ../requirements.txt -i https://mirrors.aliyun.com/pypi/simple
cd ..

# 3) （可选）重新构建前端：产物输出到 backend/zhishu/static
cd frontend && npm install && npm run build && cd ..
```

### 7.2 启动

```bash
cd backend && python start_backend.py
# 浏览器打开 http://127.0.0.1:8080
```

> **访问入口**：直接打开 **FastAPI 后端地址（默认 http://127.0.0.1:8080）**，后端同源托管前端页面与 `/api/v1/*` 接口。
> 不要通过 `npm run preview`（4173）访问——那是纯静态预览，没有后端 API。
> 默认账号 `admin` / `zhishu@2026`（生产必须修改 `deploy/zhishu.yaml` 的 `admin_password`）。

---

## 八、关键配置项（deploy/zhishu.yaml）

| 配置段 | 关键字段 | 说明 |
|--------|----------|------|
| `providers`（顶层） | `<name>: {base_url, api_key, models, priority, ...}` | 多 Provider 接入；运行时增改经 `/api/v1/providers` 持久化到 `data/providers.json` |
| `default_model` | — | 全局默认模型（`provider/model`；留空则按可用 Provider 自动解析） |
| `embedding` | `backend` / `embed_model` / `fallback_hash` | 网络端点或本地；未配置降级哈希向量 |
| `security` | `secret` / `admin_password` / `enable_auth` / `outbound_allow` / `enable_sm` / `allow_private_fetch` | 签名密钥（**必改**）、出网开关、国密开关、SSRF 放行开关 |
| `web_search` | `backend` | bing_cn / duckduckgo / tavily / bing |
| `agent` | `nudge_interval` / `reflection_enabled` / `skills_auto_learn` / `parallel_tools` / `parallel_tool_workers` / `tool_fail_break` / `tool_cycle_break` / `tool_repeat_break` / `max_tool_steps` / `prompt_cache` | 自进化开关 + 推理循环优化（叶子并发 / 三级熔断 / **重复成功循环熔断** / Prompt 缓存，均默认开启或 `auto`） |
| `cron` | — | 定时任务调度配置 |
| `memory` | `vector_enabled` 等 | 长期记忆。开关仅控制**向量语义召回**（跨会话沉淀/检索需配置 Embedding，无则优雅降级为 None）；文件式记忆（`MEMORY.md`/`USER.md`/`SOUL.md` 与 `memory` 工具）属知识沉淀层，始终生效，不受此开关影响 |

完整字段见 `deploy/zhishu.yaml.example`；运行时覆盖项落盘 `data/config.override.json`（设置页）。

> ⚠️ **密钥耦合提示**：`data/providers.json` 中的 API Key 用 `security.secret` 派生密钥加密。**轮换 secret 会使已存 Key 失效**（对话报「所有 Provider 均不可用」）——轮换后需在前端「模型」页重新填入各 Provider 的 Key。

### 8.1 内网 / 私有 Embedding 模型接入

智枢的 Embedding **不是独立模型类型**，而是复用 LLM Provider 的 OpenAI 兼容 `/embeddings` 接口（`core/embedding.py`）。内网 embedding 服务（vLLM / SGLang / Xinference / Ollama / 本地 bge 等）都按**一个 Provider** 接入：

**步骤一：模型管理添加 Provider（UI）**
- 名称（如 `emb-intranet`）、Base URL 填 `http://<内网IP>:<端口>/v1`、API Key 无鉴权则留空；
- 默认模型 / 模型列表填该服务 `/embeddings` 实际接受的模型名（如 `bge-m3`）；
- 优先级调大（避免抢默认聊天模型）；内网无密钥端点打开「本地模型」（不出网）开关；
- 推理框架选对应项（vLLM / Xinference / MindIE …）以规避协议差异。

**步骤二：在 `deploy/zhishu.yaml` 指定 embedding 指向（必须，重启生效）**
> ⚠️ 前端「模型管理」**没有** `embedding.provider` / `embedding.embed_model` 的输入框，这两个开关只能写在 YAML 里（运行时 `/api/v1/settings` 也不管 embedding）。

```yaml
embedding:
  backend: provider          # 走 Provider 的网络 /embeddings
  provider: emb-intranet     # 须与步骤一的名称一致
  embed_model: bge-m3        # 须与该端点 /embeddings 接受的 model 完全一致
  fallback_hash: true        # 服务不可用时优雅降级为哈希向量，不中断流程
```

**步骤三：重启并验证**
```bash
docker restart zsagent
```
- 知识库解析文档后，`embedding_dim` 应变为真实维度（如 bge-m3=1024），不再是默认 512（哈希降级）。
- 设置页「长期记忆」的跨会话语义召回开关应能开启（依赖可用 Embedding）。

**其他 backend（纯内网免 Provider）**：`backend: ollama`（`ollama_base` / `ollama_model`）、`backend: local`（`model: bge-small-zh`，需容器装 torch）、`backend: hash`（默认降级，无语义能力）。

**常见坑**：① `embed_model` 名须与服务端完全一致（vLLM `--served-model-name` 改名后此处也要改）；② 仅加 Provider 不写 YAML `embedding:` 段 = 仍走哈希降级；③ 改 YAML 必须重启；④ 向量空间签名隔离（`emb_sig`）保证降级哈希向量不会污染真实语义检索库。

---

## 九、Docker 容器化部署

镜像为「单进程」设计：一个容器同时跑 Agent 引擎 + API + 前端，对外仅暴露 `8080`。

### 9.1 准备配置

```bash
cp deploy/zhishu.yaml.example deploy/zhishu.yaml
# 填入 api_key、管理员密码，并把 security.secret 改为强随机值
```

### 9.2 构建镜像

```bash
# 标准（需 docker.io 可达）
docker build -t zhishu-agent:1.0.32 -f deploy/Dockerfile .

# 受限网络 / 国内零代理（推荐内网、本机）
docker build -t zsagent:1.0.32 -f deploy/Dockerfile.local .
```

### 9.3 运行

```bash
docker run -d --name zsagent \
  -p 8080:8080 \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped \
  zsagent:1.0.32
```

- `zsagent_data` 卷持久化：向量库、会话记忆、`providers.json`、用户库、媒体文件。
- 打开 http://localhost:8080 ，默认 `admin` / `zhishu@2026`（生产必改）。

### 9.4 配置不烧进镜像（推荐生产）

```bash
docker run -d --name zsagent -p 8080:8080 \
  -v "$(pwd)/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro" \
  -v zsagent_data:/app/backend/data \
  --restart unless-stopped zsagent:1.0.32
```

> 重建容器（`docker rm` + `run`）会丢弃可写层：请确保挂载的 `zhishu.yaml` 中 `security.secret` 与旧值一致（否则触发硬闸门或 Provider Key 失效），或启动时传 `-e ZHISHU_SECRET=<同值>`。

### 9.5 一键编排

```bash
docker compose -f deploy/docker-compose.yml up -d
```

### 9.6 健康检查、验证与排错

```bash
curl http://localhost:8080/health        # 期望 {"status":"ok",...}
docker logs -f zsagent                    # 启动 / 对话日志
python backend/tests/e2e_roles_check.py   # 四角色 RBAC / 安全闭环端到端验证

# 同一套脚本可直接验证任意远程实例（BASE 与 admin 口令用环境变量覆盖）
ZHISHU_BASE=http://127.0.0.1:8090 ZHISHU_ADMIN_P='<远程admin密码>' python3 backend/tests/e2e_roles_check.py
```

脚本是**自包含**的：缺失的 `rs_op / rs_user / rs_viewer` 测试账号由 admin 自动创建，跑完按用户 **ID** 删除并以 `cleanup:created-users` 断言校验，不会在生产实例上留下测试账号。
断言条数随环境浮动（公共 Provider 为 0 时会跳过 4 条公共 Provider 保护断言），以 `0 FAIL` 为通过标准。

- 对话报「所有 LLM Provider 均不可用 / 401 / 429」：Key 失效、配额耗尽或 secret 轮换导致解密失败——在前端「模型」页重填 Key。
- 进程启动即退出（exit 2）：`security.secret` 仍为默认值，改为强随机值后重启。
- 普通用户报「Provider 未配置或未启用」：将 Provider 设为公共（owner 置空）或按角色共享。

### 9.7 远程服务器部署（实录流程）

将整套代码部署到一台远程 Docker 主机，全流程如下（宿主机侧执行）：

```bash
# 1. 打包干净源码（含预构建的 backend/zhishu/static，远程无需 Node 环境）
git archive --format=tar.gz -o zhishu-src.tar.gz HEAD

# 2. 上传并解压
ssh -p <port> root@<host> "mkdir -p /opt/zhishu-agent"
scp -P <port> zhishu-src.tar.gz root@<host>:/opt/zhishu-agent/
ssh -p <port> root@<host> "cd /opt/zhishu-agent && tar xzf zhishu-src.tar.gz && rm -f zhishu-src.tar.gz"

# 3. 远程构建镜像（耗时较长，建议 nohup 后台跑并 tail 日志）
ssh -p <port> root@<host> "cd /opt/zhishu-agent && nohup docker build -t zsagent:1.0.32 -f deploy/Dockerfile.local . > build.log 2>&1 &"

# 4. 生成生产配置：必须替换默认 secret 与 admin 口令，否则进程启动即 exit 2
#    secret 用 64 位强随机；配置以只读挂载进容器，不烧进镜像
cp deploy/zhishu.yaml.example deploy/zhishu.yaml && chmod 600 deploy/zhishu.yaml
#    编辑 security.secret 与 auth.admin_password

# 5. 启动（数据落 named volume，随宿主重启自恢复）
docker volume create zsagent_data
docker run -d --name zsagent --restart always \
  -p 8090:8080 \
  -v zsagent_data:/app/backend/data \
  -v /opt/zhishu-agent/deploy/zhishu.yaml:/app/deploy/zhishu.yaml:ro \
  zsagent:1.0.32

# 6. 回填 Provider Key（Key 与 secret 强耦合，不能直接拷贝旧密文，必须走 API 明文回填）
curl -X POST http://127.0.0.1:8090/api/v1/providers -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"name":"<provider>","base_url":"...","api_key":"...","models":["..."],"shared":true}'
curl -X POST http://127.0.0.1:8090/api/v1/models/default -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d '{"model":"<provider>/<model>"}'
```

**部署踩坑提示**

- **Provider Key 不可随卷迁移**：`data/providers.json` 中的 Key 以 `security.secret` 派生密钥混淆落盘。新实例 secret 不同，直接搬运密文会解密失败。正确做法是用明文经 `POST/PUT /api/v1/providers` 回填，由目标实例用自己的 secret 重新加密。
- **端口不通先分清是谁在拦**：宿主 `firewall-cmd --list-ports` 已放行、`ss -lntp` 也在监听，但公网仍超时，基本可判定是**云厂商安全组**未放行，需到云控制台开端口。
- **未知 `/api/v1/*` 路径会被 SPA 兜底返回 200 + `index.html`**，排障时别把 HTML 响应误判成接口连通。

---

### 9.8 国产信创部署（离线 / ARM64 / 麒麟 / UOS / openEuler）

信创（信息技术应用创新）环境普遍具备三个特征，智枢的架构对此**天然友好**：

- **离线**：构建机常无 `docker.io` / 公网直连。智枢全部 Python 依赖均为**预编译 wheel**（见仓库根 `requirements.txt`：`numpy` / `PyMuPDF` / `openpyxl` / `Pillow` / `jieba` 等均提供 manylinux / aarch64 / musllinux 预编译包，安装无需 gcc 源码编译），前端也已预构建，离线机可直接 `--no-index` 安装；且**不依赖任何境外厂商 SDK**，国产模型统一经 OpenAI 兼容协议调用。
- **异构 CPU**：鲲鹏 920 / 飞腾 FT-2000+ / D2000 为 `linux/arm64`，龙芯为 `loongarch64`，海光 / 兆芯为 `x86_64`。
- **合规**：数据不出内网、国密（`security.enable_sm`，SM2/SM3/SM4，缺 gmssl 自动降级）、弱密钥启动硬闸门、SSRF 出网闸门（`security.outbound_allow` / `allow_private_fetch`）、审计日志与脱敏——均已在代码中落地。

#### 9.8.1 镜像架构适配

```bash
# x86_64 信创（海光 / 兆芯）—— 沿用现有镜像
docker build -t zsagent:1.0.32 -f deploy/Dockerfile.local .

# ARM64（鲲鹏 920 / 飞腾）—— 指定平台交叉构建
docker build --platform linux/arm64 -t zsagent:1.0.32-arm64 -f deploy/Dockerfile.local .
```

- 纯 wheel 依赖意味着在 ARM64 / LoongArch 下 `pip install` 不会触发本地编译；**但生产务必在「目标架构的同源机器」上原生构建**（鲲鹏机上直接 `docker build`），避免 qemu 仿真带来的性能与稳定性损耗。
- 基础镜像必须走华为云 SWR 镜像源（`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/<image>`），因信创 / 内网构建环境 `docker.io` 不可达（见项目 `deploy/Dockerfile` / `deploy/Dockerfile.local` 注释）。

#### 9.8.2 离线分发包（无外网环境）

在**可联网的同架构机器**上生成离线包，再经光盘 / 内网摆渡机拷到信创主机：

```bash
# 在同源架构联网机执行：产物为 zhishu-offline-<date>.tar.gz（含 wheels / 预构建前端 / 配置 / install-offline.sh）
bash deploy/offline-build.sh

# 拷到信创主机后：解压 → 安装 → 启动
tar xzf zhishu-offline-<date>.tar.gz
cd zhishu-offline-<date>
bash install-offline.sh        # 自动建 venv、pip --no-index 装 wheels、启动服务
```

- 离线包内**不含任何密钥**：运行前按 9.1 / 9.4 准备 `zhishu.yaml`，`security.secret` 必为强随机。
- 国密开关 `security.enable_sm=true` 默认开启（Provider Key 以 SM4 混淆、摘要以 SM3），满足信创测评项；仅在无国密合规诉求时可关。
- ⚠️ **`wheels/` 必须与目标 CPU 架构一致**：x86 机生成的 wheels 无法在 ARM64 信创机上 `--no-index` 安装，反之亦然。务必在同架构联网机跑 `offline-build.sh`。

#### 9.8.3 适配国产操作系统

| 操作系统 | 基础镜像 / 注意事项 |
|---------|------------------|
| 银河麒麟 V10 / 中标麒麟 | CentOS 系 glibc；`python:3.11-slim` 经 SWR 源可跑，留意 glibc 版本匹配 |
| 统信 UOS | Debian 系；`python:3.11-slim` 直接可用，确认 `libgomp` 等运行时库存在 |
| openEuler / 麒麟 V10 SP3 | 原生支持 ARM64；建议在鲲鹏机上原生 `docker build` 直接产出 arm64 镜像 |
| 鲲鹏 / 飞腾整机 | 见 9.8.1 的 `--platform linux/arm64` 构建 |

#### 9.8.4 信创合规加固清单（上线前核对）

- [ ] `security.enable_sm=true`：Provider Key 以 SM4 混淆、摘要以 SM3，满足国密算法合规项。
- [ ] `security.secret` 为 64 位强随机，且容器**未**注入 `ZHISHU_ALLOW_INSECURE_DEFAULTS=1`——否则进程应拒绝启动（硬闸门）。
- [ ] `security.outbound_allow=false`（默认）且 `allow_private_fetch=false`（默认）：工具出网关闭、模型列表拉取禁止私有地址，构成 SSRF 出网闸门，满足「数据不出域」。
- [ ] 数据落 `zsagent_data` 卷且不随容器销毁丢失；审计日志可归档。
- [ ] 替换默认 `admin` 口令，并按 RBAC 收敛 operator / user / viewer 权限。
- [ ] 如需接入国产关系库（达梦 `dmPython` / 人大金仓 `kingbase8`，`requirements.txt` 已预留注释）：当前默认 SQLite（零依赖、可离线），替换属二次开发项，需在 `core/db.py` 抽象层增加方言适配，不在本镜像范围。

#### 9.8.5 信创部署常见排查

- **镜像拉取 / 构建超时**：确认 `Dockerfile.local` 基础镜像走 SWR 源；`docker.io` 在信创内网不可达。
- **ARM64 启动报 `exec format error`**：镜像与 CPU 架构不符（x86 镜像跑在 ARM 机），按 9.8.1 构建对应平台镜像。
- **进程启动即 exit 2**：`security.secret` 仍为默认值，改强随机后重启。
- **离线安装 `pip` 报包找不到**：`wheels/` 与目标架构不符；重新在同架构联网机生成离线包。

#### 9.9 OpenAI 兼容服务端网关（对接开源前端生态）

智枢内置一个 **OpenAI 兼容网关**，可让 Open WebUI / LobeChat / 任意兼容 OpenAI 协议的开源前端与 SDK 直接把智枢当作「模型后端」使用，且**复用现有 RBAC**——无需为网关另建账号体系。

> 设计取舍：网关走「直连 LLM 流式」（`/v1/chat/completions` + SSE），给出标准 OpenAI 语义：客户端自管历史（`messages` 数组），服务端只透传模型 token。智枢自身的 RAG / 工具 / 系统提示等 agent 能力由原生 Web UI 提供；网关层保持纯粹，避免与客户端自带的 system prompt / 工具系统冲突。

**端点**

| 方法 | 路径 | 说明 | 所需权限 |
|---|---|---|---|
| `GET` | `/v1/models` | 返回当前用户可见的模型列表（OpenAI 标准 `object=list` 格式，`id` 形如 `provider/model`） | `models:read` |
| `POST` | `/v1/chat/completions` | 对话补全，支持 `stream=true`（SSE，标准 `chat.completion.chunk` + `data: [DONE]`）与非流式；`tools` 会被透传并规范化为 OpenAI `delta.tool_calls` | `chat` |

**鉴权**：与对话页一致，请求头 `Authorization: Bearer <token>`，`<token>` 即 Web UI 登录后拿到的会话令牌（管理后台「用户」页可创建/吊销）。未带或失效令牌一律 `401`。普通用户仅能调用其**可见**（本人 + 共享 + 角色命中）的 Provider/模型，越权调用被拒。

**错误映射**（OpenAI 兼容，便于客户端自动退避）：上游 Provider 限流（`HTTP 429`）→ 网关返回 `429 rate_limit_error`；Provider 鉴权失败/缺 API Key → `401 authentication_error`；参数错误 → `400 invalid_request_error`；其余上游异常 → `500 server_error`。并发与单用户每日配额由智枢既有三重限流器统一管控，超额同样以 `429 rate_limit_error` 返回。

**在 Open WebUI / LobeChat 中接入**

1. 打开客户端「设置 → 模型 / 外部连接」，选择「OpenAI 兼容」类型；
2. API Base URL 填智枢地址，例如 `http://<智枢服务器>:8080/v1`；
3. API Key 填你的智枢会话令牌（即 `Authorization` 里的 `<token>` 部分，不含 `Bearer `）；
4. 保存后在模型下拉里即可看到 `/v1/models` 返回的 `provider/model`，选定即可对话。

**请求示例（curl）**

```bash
# 列出可见模型
curl -H "Authorization: Bearer $ZS_TOKEN" http://localhost:8080/v1/models

# 流式对话
curl -N -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $ZS_TOKEN" -H "Content-Type: application/json" \
  -d '{"model":"<provider>/<model>","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

**合规提示**：网关与 Web UI 共用同一套 RBAC 与审计日志（每次调用记入审计），适合在需要「统一账号、统一审计、统一模型出口」的内网/信创环境内，把智枢作为团队共享的 OpenAI 兼容底座。

---

## 十、模块功能闭环清单（本次全量审计）

下列每个模块均已完成「后端 CRUD + RBAC 鉴权 + owner 隔离 / 三态共享 + 前端视图与写门控 + 持久化」的完整闭环，并经 `backend/tests/e2e_roles_check.py` 四角色端到端验证（**18/18 PASS**）。

| 模块 | 后端端点（`api/`） | 权限点 | 隔离 / 共享 | 前端视图 | 持久化 |
|------|--------------------|--------|-------------|----------|--------|
| 认证 / 用户 | `/auth/*`、`/users/*` | `users:read/write`（仅 admin） | 用户库按账户隔离 | LoginView / UsersView | `zhishu_users.db`（SQLite） |
| 对话 / 会话 | `/chat`、`/conversations` | `chat` | 会话按 `owner`；admin 可切「全部 / 我的」 | ChatView + chat store | `zhishu_conversations.db` |
| 知识库 / RAG | `/knowledge` | `knowledge:read/write` | 文档按 `owner`，`owner` 空=公共可见 | KnowledgeView | 向量库 SQLite |
| 模型 / Provider | `/models`、`/providers` | `models:read/write` | `owner` 空=公共（仅 admin 改/删）；无 Key 云端 Provider 前端自动过滤 | ModelsView（ProvidersPanel / ProviderCard） | `data/providers.json`（Key SM4/XOR 混淆） |
| 插件 | `/modules/plugins` | `modules:read/write` | 默认私有；私有 / 角色 / 公共三态；非 owner 且非 admin 只读 | PluginsView | `data/plugins/<name>` |
| 技能 | `/modules/skills` | `modules:read/write` | 同上 | SkillsView | `data/skills/<name>` |
| MCP | `/mcp` | `modules:read/write` | 同上（含连接测试 / 工具调用测试） | McpView | `data/mcp/<name>` |
| 智能体 | `/agents` | `agents:read/write` | 同上；普通用户仅可编辑**本人所拥有**的智能体 | AgentsView | `data/agents` |
| 定时任务 | `/cron` | `cron:read/write` | 任务按 `owner`；`shell` 动作仅 admin/operator | CronView + cron store | `data/cron` |
| 设置 | `/settings` | `admin` | 长期记忆开关等运行时覆盖 | SettingsView | `data/config.override.json` |
| 系统 / 审计 / 脱敏 | `/admin/*` | `system:read` / `audit:read` | 审计脱敏后落库 | SystemView | 审计库 |
| 记忆 | `/modules/memory` | `modules:read/write` | 长期记忆按用户 | MemoryView | `MEMORY.md` / `USER.md` / `SOUL.md` |

**本轮重点修复（2026-07-30）**：
- 修正技能 / 插件 / MCP / 智能体视图按 `owner` 判定「可编辑」的字段取值（使用 `app.user.user` 而非未定义的 `app.user.username`），**普通用户现已能正常编辑 / 删除自己创建的模块**。
- 管理员「切换用户」(X-Act-As) 后，侧边栏模型选择器随身份同步刷新，避免与普通用户视角下聊天页模型列表不一致。
- 补齐 `Provider` 类型契约（`has_key` / `api_key_masked` / `priority` / `builtin`），移除与运行时契约冲突的过期 `CronJob` 重复类型。
- 后端 RBAC 矩阵对 admin 专属端点（`users` / `system` / `admin` / `settings`）补充显式声明，提升权限可读性（`*` 仍兜底，功能不受影响）。
- 端到端验证脚本改为**自包含**：缺失的四角色测试账号由 admin 自动创建并在结束后清理，可对任意新容器重复运行。

**已知设计决策**：知识库文档共享目前采用「`owner` 空=公共」单一维度（与其他模块「公共」概念一致），未实现「按角色共享」三级粒度；如需可按 `share_with` 模式扩展 `vector_store` 文档表。

---

## 十一、安全审计与多用户架构（2026-08-03）

### 11.1 全量代码审计与漏洞修复

对 `backend/zhishu/` 与 `frontend/src/` 做了静态全量审计（AST 扫描 + 人工走查 + 容器内单元/集成验证），本轮修复如下：

| 编号 | 风险 | 位置 | 处置 |
|------|------|------|------|
| V1 | 前端存储型 XSS（Markdown 表格单元格未转义即 `v-html`） | `frontend/.../MarkdownRenderer.vue` | 单元格内容统一走 `inline()` 转义 |
| V2 | SPA fallback 路径穿越（`../` 越权读 `data/providers.json` 等） | `backend/zhishu/main.py` | 改用 `realpath` 归一化并校验位于 static 目录内 |
| V3 | 国密缺失时密钥降级 XOR 混淆（弱加密） | `core/security.py` | 启动时打印安全告警，建议安装 `gmssl` |
| V4 | 多 worker 破坏进程内单例（限流/配额/定时任务重复） | `main.py` | 启动时对 `workers>1` 打印架构告警，建议 `workers=1` |
| V5 | 上传无大小上限（全量 `file.read()` 打满内存） | `api/knowledge.py` / `api/chat.py` | 新增 `core/upload.read_upload_limited` 分块读取，超 100MB 返回 413 |
| V7 | `safe_web_fetch` / 插件 http 无内网 IP 过滤 | `core/tools/builtins/web.py` / `core/modules/plugins.py` | 统一接入 `core/ssrf.guard_url` 拦截内网/私有/回环/云元数据 |
| V9 | 脱敏失败 fail-open 回退原文（可能泄露 PII） | `core/redact.py` | 改为 fail-closed，异常时整体隐藏并记录日志 |

V6（插件 shell 命令注入）、V8（operator 任意 shell/cron）属设计性 RCE，已通过角色闸门（`TOOL_MIN_ROLE` 要求 operator、`action=shell` 仅 admin/operator 可建）与出网/沙箱约束收敛；生产建议默认关闭 `allow_code_exec` 并对 shell 类动作加强审计。V10（默认密钥/口令）已由启动硬闸门与告警覆盖。

### 11.2 多用户架构与 FastAPI 适配性分析

**结论：FastAPI（ASGI）本身非常适合多用户并发场景；问题不在框架，而在当前「单进程 + 进程内可变单例」的状态管理方式。**

- **适配性优势**：FastAPI 基于 Starlette/asyncio，对 I/O 密集（LLM 流式、工具调用、文件落盘）的多用户并发吞吐表现良好；本项目的 RBAC、`owner` 归属、媒体网关、审计均已按多用户设计。
- **当前约束（单进程状态）**：`AppContext`、`ToolRegistry`、`ConcurrencyLimiter`、`CronScheduler`、SQLite 连接与对话缓存均为**进程内单例**。在 `workers=1` 下完全正确；若将 `workers` 设 >1 或横向扩多副本，各进程状态独立，会导致限流/配额/定时任务行为不一致、工具注册表漂移。
- **并发安全**：任务级身份通过 `contextvars` 透传 + `ToolContext.for_run()` 浅拷贝隔离，避免跨请求串号；`code_exec` 经 `asyncio` 子进程 + `RLIMIT_AS` + 超时强杀，不阻塞事件循环。
- **推荐部署形态**：
  - **默认/内网离线（本项目定位）**：单进程 `workers=1` + 进程内状态，简单可靠、数据不出本机，契合「自主可控」。
  - **更大规模**：将限流器/配额外置到 Redis、对话/审计落到共享数据库、定时任务由单进程协调（分布式锁），再用多容器 + 负载均衡水平扩展。本项目已为这一步预留了 `init_limiter` / `CronScheduler` 等注入点。

### 11.3 端到端业务流程

完整链路：浏览器同源调用 → 登录鉴权（HMAC Token + 四角色 RBAC + X-Act-As 代管）→ 对话入口（SSE 流式、owner 隔离）→ Agent ReAct 循环（上下文组装 / LLM 多 Provider 回退 / 解析 tool_calls）→ 工具执行层（沙箱路径隔离、审计、出网门控、SSRF 内网拦截、代码执行 RLIMIT+超时）→ 结果回填与思维图谱 → 异步闭环（技能蒸馏、记忆反思、定时任务、多智能体委派）。多用户通过 `owner` 归属 + `contextvars` 隔离 + 浅拷贝 + 命名空间保证彼此不可越权。

### 11.4 目录清理（本轮）

移除仓库根目录与 `backend/` 下遗留的调试/临时产物（`verify_*.py`、`_*.py` 测试脚本、`*.log`、`_cell_graph_preview.html` 等）与未使用的 `.venv-test`，不触及 `data/`（运行时数据）、`.venv/`、`frontend/node_modules/`、`backend/zhishu/static/`（构建产物）。

## 十二、全链路闭环审计与修复（2026-08-03）

在第十一节安全审计之上，本轮对**后端 core / 后端 API / 数据与持久化层 / 前端**四大面做了逐模块业务闭环走查，重点排查「入口 → 处理 → 持久化 → 反馈」链路上的断点（数据被静默破坏、资源不回收、状态不一致、删除不级联等）。

### 12.1 已修复的闭环断点

| 编号 | 断点 | 位置 | 影响 | 处置 |
|------|------|------|------|------|
| C1 | 对话部分更新把未传字段写成 `"null"` | `core/conversations.py` | 只改标题会清空全部消息，列表接口 `len(None)` 抛 500 | `update` 跳过 `None` 字段；`_row` 对反序列化结果做 `isinstance(list)` 兜底 |
| C2 | 轮换 `security.secret` 后 Provider 密钥被静默清空 | `core/credentials.py` | 全部 LLM 调用 401，且原密文不可恢复 | 新增 `_raw` 原始密文缓存，明文解不出时**保留原密文**而非写空 |
| C3 | 令牌无吊销机制 | `core/security.py` | 用户删除/停用/降级后旧令牌仍有效至 7 天 | `verify()` 回查用户库：不存在或非 active 拒绝；角色以库中当前值为准（无用户库的引导期/离线模式跳过，避免锁死） |
| C4 | 委派兜底 handler 丢失递归深度与角色 | `core/tools/builtins/delegate.py` | 子智能体可无限递归委派、越权使用工具 | 补齐 `delegate_depth` / `user_role` / `is_admin` 透传 |
| C5 | 内置工具自发现静默吞异常 | `core/tools/registry.py` | 单个 builtins 模块导入失败 → 工具全量消失且永不重试 | 改 fail-loud：记录 exception 日志，失败时不置 `_discovered`，允许后续重试 |
| C6 | 并发信号量取消时许可泄漏 | `core/concurrency.py` | 请求被取消后全局许可永久丢失，最终全站死锁 | 获取过程包裹 try/except，异常时回滚已获取许可再抛出 |
| C7 | cron 对 `sqlite3.Row` 调 `.get()` | `core/cron.py` | 定时对话任务角色回查必抛 `AttributeError` | 改为 `dict(row).get("role")` |
| C8 | 删除对话不级联清理 | `api/conversations.py` | 记忆 turns 与附件成为孤儿，长期膨胀 | 删除时清理 `memory` 中 `{owner}:{cid}` 前缀 turns + `attachments/<owner>/<cid>` 目录 |
| C9 | 删除用户不级联清理 | `api/users.py` | 对话/Provider/记忆/定时任务/知识库文档/本地目录全部残留 | 重写为「删用户 → 级联清理六类资源 → 审计埋点」 |
| C10 | 记忆前缀删除未转义通配符 | `core/memory/sqlite_provider.py` | 用户名含 `_`/`%` 会误删他人数据 | 新增 `clear_session_prefix`，`LIKE ... ESCAPE '\'` 转义 |
| C11 | `SkillsView`/`PluginsView` 未导入 `NTag` | `frontend/src/views/` | naive-ui 未全局注册，只读模式标签渲染失败 | 补齐 import |
| C12 | `streamChat` 绕过统一 `request()`，不带 `X-Act-As` | `frontend/src/api/chat.ts` | admin 代管他人时对话仍以自己身份发出 | 发送前注入 `X-Act-As` 头 |
| C13 | 设置/系统接口缺 `skipActAs` | `frontend/src/api/settings.ts`、`system.ts` | 代管态下读回的是被代管者的系统状态 | 管理端接口统一加 `skipActAs: true` |
| C14 | `ShareScopeSelector` 选「按角色共享」被弹回「私有」 | `frontend/src/components/modules/` | 角色级共享无法保存 | 加 `internal` 标志位打断 props↔state 回环 |
| C15 | `AgentsView` 缺 `sub_agents` 字段 | `frontend/src/views/AgentsView.vue` | 协调者无法在 UI 上配置成员智能体 | 表单新增「成员智能体（协调者）」多选项，读写全链路补齐 |

### 12.2 回归验证

新增两套可复现的验证脚本，均在运行容器 `zsagent` 内执行：

- `backend/tests/test_closure_audit.py` — 模块级闭环回归，**75/75 通过**（覆盖 C1–C15、令牌吊销引导期兼容，以及本轮新增的 #339 多智能体身份/工具裁剪、#340 共享连接池、#338 Shell 闸门、#341 向量签名隔离、#342 context_length 接入）。
- `backend/tests/http_closure_check.py` — 真实 HTTP 全链路验证，**24/24 通过**（对话部分更新不破坏消息、列表接口不再 500、删除级联生效、伪造但签名合法的令牌被 401 拒绝、协作工具已注册、#338 operator shell 任务拦截 `cat /etc/shadow` 且 `echo` 正常执行不泄露密钥、#341 `/knowledge/stats` 返回 `embedding_signature` 与 `stale`）。
- 既有 `backend/tests/test_multiagent_e2e.py` **6/6 通过**（含委派路由 A/B 与超时熔断），确认无回归。
- 真实对话冒烟：容器内 SSE 请求 `/api/v1/chat`，回复「连接正常」，证明 #340 共享连接池在真实流式场景下工作正常。

### 12.3 深度审计闭环（#338–#342，原遗留观察已全部闭环）

上一轮 12.3 列出的 5 条遗留观察，本轮已逐条完成代码闭环 + 回归验证：

| 编号 | 原遗留观察 | 处置 | 关键改动 |
|------|-----------|------|----------|
| #339 | `moa.py` 绕过 `filter_tool_specs` 且使用伪身份 `user="moa"` | 多智能体统一走 `contextvars` 真实身份 + `ToolContext.for_run()` 浅派生 + `filter_tool_specs` 裁剪，杜绝伪身份越权 | `core/agent/moa.py`、`core/agent/context_engine.py`、`core/agent/agent.py` |
| #340 | 每请求新建 `LLMClient` 且 `aclose()` 无调用点，连接池依赖 GC | 改为**全局共享连接池单例** `get_shared_http()`（双重检查加锁 + `is_closed` 惰性重建）；`main.py` lifespan teardown 显式回收 HTTP/cron/MCP 三处资源 | `core/providers/client.py`、`main.py` |
| #338 | cron shell 类任务在宿主进程裸跑，无沙箱 | 新增 `core/shellguard.py` **纵深防御闸门**：高危正则拒绝清单 + 可执行白名单（按 shell 控制算符切段校验首 token）+ 禁命令/进程替换 + 禁环境变量前缀 + 最小化子进程环境（剔除 `ZHISHU_*`/`SECRET`/`TOKEN`/`KEY` 类变量）+ 独立进程组（setsid/CREATE_NEW_PROCESS_GROUP）+ POSIX rlimit（AS/CPU/FSIZE/NPROC）+ 超时整组击杀；cron 与 terminal 工具接入 `check_command` + `run_guarded`；执行期实时回查角色（admin/operator），用户降级后旧任务即停 | `core/shellguard.py`（新增）、`core/cron.py`、`core/tools/builtins/terminal.py`、`core/config.py`（`allow_shell`/`shell_enforce_allowlist`/`shell_allowlist`/`shell_timeout`/`shell_mem_limit_mb`） |
| #341 | Embedding 降级 hash 向量与真实语义向量混入同一库 | **向量空间签名隔离**：每批向量随实际后端打 `emb_sig`（如 `hash:512` / `provider:qwen:text-embedding-v3:1024`），检索只与同签名向量比较 + 维度守卫防 `np.dot` 崩溃；`strict_ingest=True` 时降级入库直接报错；`signature_stats()` 暴露陈旧分块 | `core/embedding.py`、`core/vector_store.py`、`core/rag.py`、`core/config.py`（`strict_ingest`） |
| #342 | 对话接口 `context_length` 入参被静默丢弃 | 入参接入上下文组装链路，按模型/用户真实上下文窗口截断，未填不臆造、未知模型返回 `None` | `api/models.py`、`core/agent/context_engine.py` 等 |

### 12.4 安全配置建议（生产部署）

- **Shell 类能力**：默认 `allow_shell=true` 仅用于内网可信运维；生产建议将 `shell_enforce_allowlist=true` 保留，并显式收窄 `shell_allowlist`（白名单刻意不含 `sh/bash/cmd/powershell/env/xargs/eval/sudo/chmod/ssh/nc/docker/crontab` 等提权/横向移动工具）。
- **向量库**：保持 `strict_ingest=true`，避免 hash 伪向量污染真实语义检索；跨模型/跨 Provider 检索通过 `emb_sig` 自动隔离。
- **连接池**：`workers=1` 下单例共享池零开销；若未来扩多进程，需配合外部状态后端（见 11.2），避免每进程独立池。

> 本系统为「**完整可运行的多用户智能体平台**」：RBAC 多租户、模块共享、运行时 Provider 管理、安全防护与多用户并发隔离均已实现闭环，可直接二次开发接入具体模型与数据库。

### 12.5 CI e2e 失败根因与修复（2026-08-04）

GitHub Actions `E2E Tests / e2e`（`.github/workflows/e2e.yml`，push/PR 到 `main` 时执行 `python tests/run_e2e.py --verbose`，env `ZHISHU_ALLOW_INSECURE_DEFAULTS=1`）在 commit `cec5f1f1`（「深度审计闭环 #338–#342」）引入回归，自 run 3 起全部失败（"Failed in 23 seconds"）。根因均为 **asyncio 生命周期缺陷，且只在关停阶段触发**，故表现为 `TestClient` 关停即抛 `CancelledError`——任一 e2e 套件 FAIL 即令 job 失败。

| 根因 | 位置 | 表象 | 修复 |
|------|------|------|------|
| `cron.stop()` 用 `except Exception` 捕获 `await self._task`；但 `await` 一个「已被自己 `cancel()`」的任务必抛 `asyncio.CancelledError`（继承自 `BaseException`，`except Exception` 抓不住）→ 异常冲出 `stop()` → 冲出 `lifespan` teardown → ASGI 判定 lifespan 被取消 → 关停报 `CancelledError`，且排在 `cron.stop()` 之后的 MCP 回收被整段跳过 | `core/cron.py` | `test_chat_http_e2e.py` 在 `TestClient.__exit__` 关停时抛 `CancelledError`，HTTP 套件 FAIL | `stop()` 改为 `except (asyncio.CancelledError, Exception)` |
| `main.lifespan` 关停期三步资源回收（共享连接池 / cron / MCP 客户端）均用 `except Exception`，且未持有启动期后台任务引用——GC 可能提前回收，或在任务取消时泄漏 | `main.py` | 同上，且关停阶段资源泄漏 | 每步改为 `except (asyncio.CancelledError, Exception)` 自包含；新增 `_boot_tasks` 列表持有启动期任务引用，teardown 对未完成任务统一 `cancel()` + `await` 回收 |
| HTTP e2e 桩 `FakeLLM.chat` 缺 `tool_choice` 形参，而 `LLMClient.chat` 已新增该参数 → 真实调用抛 `TypeError` 被兜底成 error 事件，表象为「回复缺失」 | `tests/test_chat_http_e2e.py` | 自签 token 全链路对话用例失效 | 补 `tool_choice=None, **_kw` 兼容新签名 |
| 向量库路径 `RagConfig.path="data/zhishu_vector.db"` 为硬编码相对 CWD 裸路径，不跟随 `server.data_dir`，测试无法用临时目录隔离 → 串到仓库真实数据（本地曾因 17.8GB 真实向量库导致锁等待超时） | `core/rag.py` + `core/config.py` | 本地/多实例测试串数据、偶发 `database is locked` | `KnowledgeBase` 新增 `_resolve_store_path`：绝对路径原样保留，相对路径去掉重复 `data/` 前缀后拼到 `data_dir` 下；默认配置落点零迁移 |

附带清理：`backend/` 下调试/临时产物（`_diag_*.py`、`_dbg_*.txt`、`_e2e_run.txt`）已删除，不入库。

修复后验证（与 CI 干净环境等价复跑）：

- `python tests/run_e2e.py --verbose`（env `ZHISHU_ALLOW_INSECURE_DEFAULTS=1`）：`test_multiagent_e2e.py` **6/6 PASS** + `test_chat_http_e2e.py` **A/B/C 3 项 PASS** → **ALL E2E SUITES PASSED**。
- `backend/tests/test_closure_audit.py`：新增 3 个针对本根因的回归测试（`test_cron_stop_no_cancelled_error` / `test_lifespan_teardown_isolated` / `test_vector_store_follows_data_dir`），**89/89 断言通过**。
- 既有 `backend/tests/http_closure_check.py` 24/24 不受影响。
- 复查补充：`core/modules/mcp.py` 的 `MCPClient.close()` 取消 `_reader_task` 后未 `await`（同类隐患，未触发 e2e 因测试不连接 MCP client），已补 `await` + `except (asyncio.CancelledError, Exception)` 与 `cron.stop` 一致。

### 12.6 审计缺口修复（2026-08-09）

在 12.1–12.5 业务闭环审计之上，本轮对「认证 / 记忆 / 脱敏 / 前端」四个面补齐 5 项闭环缺口（编号 G1–G6，其中 G5 为误报已跳过），均经 `backend/tests/test_audit_gaps_fix.py` 回归验证，并配套产出 `AUDIT_REPORT_2026-08-09.html`：

| 编号 | 缺口 | 位置 | 处置 |
|------|------|------|------|
| G1 | 向量长期记忆不可观测 / 不可清理 | `core/memory/backends.py` / `manager.py` / `vector_provider.py` | 新增 `stats()` / `clear()`；新增 `GET/DELETE /api/v1/memory/vector`（`modules:write` 守卫，非 admin 限定本人 `owner`） |
| G2 | 令牌无主动吊销 / 登出 | `core/security.py` / `api/auth.py` / `api/users.py` | 引入 `jti` + `revoked_tokens.json` 持久化；新增 `POST /auth/logout`、`POST /users/{uid}/revoke`（`bump_epoch` 强制失效该用户全部令牌） |
| G3 | 统一鉴权守卫缺 `skip_act_as`；`require_auth` 定义顺序致导入 `NameError` | `api/auth.py` | `require_auth` 支持 `skip_act_as`（`/me`、`/change-password`、`/logout` 关闭 X-Act-As 穿透，避免自改密码 / 登出被代管误影响）；前移定义修复「默认参数在定义前求值」导致的导入崩溃 |
| G4 | 前端消费 `/auth/status` 死代码（部署模式 / 用户数提示） | `frontend/src/views/LoginView.vue` | 移除未使用的 `authStatus` 调用与登录页部署提示文案（避免泄露部署形态 / 用户规模） |
| G6 | 脱敏命中无统计 | `core/redact.py` / `api/admin.py` | `Redactor` 增 `calls` / `masked` 计数；新增 `GET /admin/redact/stats` |

> G5（模型持久化）经核查为误报——`app.selectedModel` 已落盘 `localStorage`，跳过。
