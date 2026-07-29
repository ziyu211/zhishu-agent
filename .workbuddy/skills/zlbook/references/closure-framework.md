# 业务功能闭环方法论（详细）

## 一、五阶段闭环展开

### 1. 权限定义
- RBAC 的 `can(role, perm)` 实现：`*` 恒真；写权限隐含读（`chat:write` ⇒ `chat:read`）。
- 每个模块的能力必须落在某个权限 key 上（如 `knowledge:write`、`users:read`）。
- 判断断点：某功能在 UI 上能点，但 `can()` 没对应 key → 权限定义缺失。

### 2. 后端实现
- 每个路由必须有真实逻辑，无 stub。重点排查：
  - `pass` / `...` / `return {}` 占位
  - `raise NotImplementedError`
  - 仅 `__init__` 实例化、从不被调用的方法 / 类（死代码，如 `CredentialPool`）
- 安全项：CORS（`allow_origins=["*"]` 时 `allow_credentials` 必须为 `False`）、`secret` / `admin_password` 默认值自检告警、路径穿越、批量接口缺失。

### 3. 前端可见性
- 视图按 `can()` 结果显隐按钮 / 菜单 / 路由。
- 判断断点：有权限的用户看不到入口 → 可见性缺失（功能"存在却用不上"）。

### 4. 用户自助
- 被授权角色能端到端执行：请求方法、URL、参数形状、返回码全部正确。
- 典型断点：改密把两个字符串当 body 发出（422）；脱敏接口后端已存在但前端无入口。

### 5. 持久化
- 变更重启可复现：落盘 JSON / SQLite / 配置 override 文件，而非仅内存。
- 判断断点：设置页开关刷新即失效、无 `save()` / 无 override 加载。

## 二、智枢 13 模块清单

1. chat（对话）
2. knowledge（知识库）
3. models / providers（模型 / 供应商）
4. skills（技能）
5. plugins（插件）
6. mcp（MCP 服务 / 工具）
7. memory（长期记忆）
8. agents（子智能体）
9. cron（定时任务）
10. conversations（会话）
11. users（用户管理）
12. admin / audit（管理与审计）
13. auth / system / settings（鉴权 / 系统页 / 设置页）

## 三、逐模块检查清单模板

对每个模块复制下面清单，逐项打勾；未打勾即断点：

```
[ ] 权限    —— can() 覆盖该模块关键能力
[ ] 后端    —— 端点 FULLY 实现，无 stub / 死代码
[ ] 前端    —— 入口按角色显隐正确
[ ] 自助    —— 授权角色可端到端执行（参数 / 返回码正确）
[ ] 持久化  —— 变更重启可复现
[ ] 验证    —— 四角色 token 实测通过
```

## 四、三路并行审计落地要点

- 后端审计：用 Grep 找 `def .*router|@router\.(get|post)` 与 `pass` / `TODO` / `NotImplementedError`；统计模块数是否齐全。
- 前端审计：统计 `api.xxx()` 调用数，交叉比对真实路由；检查 `stores/*.ts` 的 `load()` 是否把 `{key:[...]}` 包裹整体赋给数组。
- 契约交叉：逐条比对 前端 `request<T>('/api/v1/...')` 与后端 `@router` 的 path/method；标记类型谎言。
