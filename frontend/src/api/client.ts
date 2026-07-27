/**
 * 智枢智能体 —— API 聚合入口
 * 保持 `import { api } from '@/api/client'` 向后兼容：所有按域拆分的方法在此聚合成 `api` 对象，
 * 同时按需 `export *` 暴露独立函数，便于新代码直接 `import { listAgents } from '@/api/agents'`。
 */
import * as auth from './auth'
import * as models from './models'
import * as users from './users'
import * as chat from './chat'
import * as knowledge from './knowledge'
import * as system from './system'
import * as skills from './skills'
import * as plugins from './plugins'
import * as mcp from './mcp'
import * as agents from './agents'
import * as memory from './memory'

export * from './http'
export * from './types'
export * from './auth'
export * from './models'
export * from './users'
export * from './chat'
export * from './knowledge'
export * from './system'
export * from './skills'
export * from './plugins'
export * from './mcp'
export * from './agents'
export * from './memory'

export const api = {
  ...auth.authApi,
  ...models.modelsApi,
  ...users.usersApi,
  ...chat.chatApi,
  ...knowledge.knowledgeApi,
  ...system.systemApi,
  ...skills.skillsApi,
  ...plugins.pluginsApi,
  ...mcp.mcpApi,
  ...agents.agentsApi,
  ...memory.memoryApi,
}
