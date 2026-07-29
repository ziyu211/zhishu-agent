---
name: zlbook
description: 智枢 zhishu-agent 平台的「业务功能闭环」审查与优化方法论（蒸馏思维框架）。当用户要求对 zhishu-agent 的功能模块逐模块审查、使其形成「权限→后端→前端→自助→持久化」完整业务闭环，或排查「功能存在却用不上」的断点时触发；也适用于把一次完整工程实践蒸馏成可复用的思维框架 / 检查清单。
agent_created: true
---

# zlbook — 智枢业务功能闭环蒸馏框架

## Overview

把智枢 zhishu-agent 平台多轮「逐模块业务闭环」工程实践蒸馏成的可复用方法论。一个功能模块只有走完 **五阶段闭环**（权限定义 → 后端实现 → 前端可见性 → 用户自助 → 持久化）才算真正落地。本 skill 提供：闭环审查清单、三路并行审计法、已知断点目录、以及构建 / 热更新 / 四角色验证的部署 playbook。

## 五阶段业务闭环（核心判定标准）

对每个功能模块，逐一确认五个阶段是否齐全，**任一缺失即为断点**：

1. **权限定义** — 角色 / 权限已定义（RBAC `can()` 覆盖该能力；`*` 恒真、写权限隐含读）。
2. **后端实现** — 真实端点存在，无 stub / 死代码（grep `pass` / `TODO` / `NotImplementedError` / 仅实例化从不消费的类）。
3. **前端可见性** — UI 入口按权限显隐（无权限角色隐藏 / 禁用）。
4. **用户自助** — 被授权角色能端到端真正执行该操作（参数形状、返回码都对）。
5. **持久化** — 变更可重启复现（落盘文件 / DB，而非仅内存）。

## 三路并行审计法（高效定位断点）

同时跑三条只读通道（用 Explore / Plan 子代理或并行 Grep）：

- **后端审计**：每个模块是否 FULLY 实现？有无 stub / 死代码？安全项（CORS、`secret` 默认值、路径穿越、批量缺失）。
- **前端审计**：每个视图是否调用真实 API（无 phantom / 422 形参调用）？有无「死 store」（`load()` 把包裹响应整体赋给数组 ref）？
- **契约交叉**：每个前端调用是否命中真实后端路由（path + method + 响应形状）。重点标记「对象包裹 vs 裸数组」的类型谎言。

## 已知断点目录（zhishu-agent 实战踩坑）

| 断点 | 现象 | 修复 |
|------|------|------|
| 改密 422 | 前端传两个字符串，后端要 `{old_password,new_password}` | 改对象传参 |
| 扫描件 PDF 404 | 渲染图写到非 media 目录，URL 却指向 `/media/...` | 写入 media 托管目录 |
| CORS 矛盾 | `allow_origins=["*"]` + `allow_credentials=True` 浏览器禁止 | 设 `allow_credentials=False`，加弱口令 / 弱密钥启动自检 |
| 插件改动不即时 | 注册仅在 refresh 时触发 | 写操作后加 `_sync_plugins()` 即时注册 |
| 死代码 | 类被实例化但从未被消费（如 `CredentialPool`） | 删除 |
| 批量缺失 | N 段文本串行发 N 次 HTTP | 一次批量请求，失败退化为逐条 |
| 三元优先级 bug | `(a or b or c if cond else None)` 在 cond 为假时丢弃真实 `a` | 拆开，优先取顶层字段 |
| 契约谎言 | 前端类型声明裸数组，后端返回 `{key:[...]}` | 诚实包裹类型 + store `load()` 正确解包 |

## 构建 / 热更新 / 四角色验证（部署 Playbook）

详细命令见 `references/deploy-verify-playbook.md`。关键陷阱：**docker cp 目录嵌套** —— 把 `api` 拷进已存在的 `api/` 会嵌套成 `api/api/`，导致提交的后端修复根本没生效。务必先 `rm -rf` 容器内目标目录，再 `docker cp <src> <container>:/app/backend/zhishu/`（拷到父目录）。

## 四角色权限矩阵（验证口径）

- `admin` = `["*"]`；`operator` = 宽泛读写但无用户管理；`user` = 受限、无管理写；`viewer` = 仅 `chat` + `models:read`。
- 验证时临时建 `operator1` / `user1` / `viewer1`，跑权限矩阵后删除。关键断言：viewer 访问 `/api/v1/settings` 必须 403；admin 必须 200。

## Resources

- `references/closure-framework.md` — 完整方法论 + 13 模块清单 + 逐模块检查清单模板。
- `references/deploy-verify-playbook.md` — 前端构建、docker cp 防嵌套、四角色验证、提交规范。
