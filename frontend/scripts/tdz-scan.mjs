/**
 * TDZ 扫描器：找出「顶层 const/let 在声明之前，于同步执行路径上被引用」的致命错误。
 *
 * 背景：本项目构建命令是裸 `vite build`（未装 vue-tsc），没有任何类型/语义门禁，
 * 这类错误会被原样打进产物，在浏览器运行时抛 ReferenceError，导致整个组件渲染失败。
 *
 * 判定规则（保守，只报必然出错的情况）：
 *   在模块顶层作用域中，若一个标识符引用了顶层 const/let/class 绑定，
 *   且该引用不在任何函数体内部（函数体是惰性执行，不构成 TDZ），
 *   且引用的字符位置早于声明位置 —— 判定为 TDZ 致命错误。
 */
import { createRequire } from 'node:module'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const require = createRequire(import.meta.url)
const parser = require('@babel/parser')
const { parse: parseSFC } = require('@vue/compiler-sfc')

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')

function walkFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walkFiles(p, out)
    else if (/\.(vue|ts)$/.test(name)) out.push(p)
  }
  return out
}

/** 取出待分析的脚本源码（.vue 取 script setup / script，.ts 取全文） */
function extractScript(file) {
  const raw = readFileSync(file, 'utf8')
  if (file.endsWith('.ts')) return { code: raw, offset: 0 }
  const { descriptor } = parseSFC(raw, { filename: file })
  const blk = descriptor.scriptSetup || descriptor.script
  if (!blk) return null
  return { code: blk.content, offset: blk.loc.start.offset }
}

function parseCode(code) {
  return parser.parse(code, {
    sourceType: 'module',
    plugins: ['typescript', 'jsx', 'decorators-legacy', 'explicitResourceManagement'],
    errorRecovery: true,
  })
}

const FN_TYPES = new Set([
  'FunctionDeclaration',
  'FunctionExpression',
  'ArrowFunctionExpression',
  'ObjectMethod',
  'ClassMethod',
  'ClassPrivateMethod',
])

/** 收集顶层 const/let/class 绑定名 -> 声明起始位置 */
function collectTopLevelTDZBindings(program) {
  const map = new Map()
  const addPattern = (node, start) => {
    if (!node) return
    switch (node.type) {
      case 'Identifier':
        if (!map.has(node.name)) map.set(node.name, start)
        break
      case 'ObjectPattern':
        for (const p of node.properties) {
          if (p.type === 'ObjectProperty') addPattern(p.value, start)
          else if (p.type === 'RestElement') addPattern(p.argument, start)
        }
        break
      case 'ArrayPattern':
        for (const el of node.elements) addPattern(el, start)
        break
      case 'AssignmentPattern':
        addPattern(node.left, start)
        break
      case 'RestElement':
        addPattern(node.argument, start)
        break
    }
  }
  for (const stmt of program.body) {
    if (stmt.type === 'VariableDeclaration' && (stmt.kind === 'const' || stmt.kind === 'let')) {
      for (const d of stmt.declarations) addPattern(d.id, stmt.start)
    } else if (stmt.type === 'ClassDeclaration' && stmt.id) {
      if (!map.has(stmt.id.name)) map.set(stmt.id.name, stmt.start)
    }
  }
  return map
}

// 含有真实运行时表达式的 TS 节点（需要继续深入），其余 TS* 节点均为纯类型，直接跳过
const TS_VALUE_NODES = new Set([
  'TSAsExpression',
  'TSSatisfiesExpression',
  'TSNonNullExpression',
  'TSTypeAssertion',
  'TSInstantiationExpression',
])
// 纯类型位置的属性键，其子树全部是类型，不产生运行时引用
const TYPE_KEYS = new Set(['typeAnnotation', 'typeParameters', 'returnType', 'typeArguments'])

/** 遍历 AST，收集「同步执行路径上」的标识符引用（跳过函数体内部与类型节点） */
function collectSyncRefs(node, bindings, refs, inFunction = false, parent = null, key = null) {
  if (!node || typeof node.type !== 'string') return
  // 纯 TS 类型节点内的标识符是类型名/参数名，不是运行时变量引用
  if (node.type.startsWith('TS') && !TS_VALUE_NODES.has(node.type)) return

  // 进入函数体后，其内部引用都是惰性的，不构成 TDZ
  const nowInFunction = inFunction || FN_TYPES.has(node.type)

  if (node.type === 'Identifier' && !inFunction) {
    // 排除非「读取变量」的标识符位置
    const isDeclName =
      parent &&
      ((parent.type === 'VariableDeclarator' && key === 'id') ||
        (parent.type === 'ClassDeclaration' && key === 'id') ||
        (parent.type === 'FunctionDeclaration' && key === 'id') ||
        (parent.type === 'ImportSpecifier') ||
        (parent.type === 'ImportDefaultSpecifier') ||
        (parent.type === 'ImportNamespaceSpecifier'))
    const isMemberProp =
      parent && parent.type === 'MemberExpression' && key === 'property' && !parent.computed
    const isObjKey = parent && parent.type === 'ObjectProperty' && key === 'key' && !parent.computed
    const isLabel = parent && (parent.type === 'LabeledStatement' || parent.type === 'BreakStatement' || parent.type === 'ContinueStatement')
    if (!isDeclName && !isMemberProp && !isObjKey && !isLabel && bindings.has(node.name)) {
      refs.push({ name: node.name, start: node.start })
    }
    return
  }

  for (const k of Object.keys(node)) {
    if (k === 'loc' || k === 'start' || k === 'end' || k === 'leadingComments' || k === 'trailingComments') continue
    if (TYPE_KEYS.has(k)) continue
    const v = node[k]
    if (Array.isArray(v)) {
      for (const c of v) if (c && typeof c.type === 'string') collectSyncRefs(c, bindings, refs, nowInFunction, node, k)
    } else if (v && typeof v.type === 'string') {
      collectSyncRefs(v, bindings, refs, nowInFunction, node, k)
    }
  }
}

function lineOf(code, index) {
  return code.slice(0, index).split('\n').length
}

const files = walkFiles(SRC)
let bad = 0
for (const file of files) {
  let ext
  try {
    ext = extractScript(file)
  } catch (e) {
    console.log(`[解析失败] ${path.relative(SRC, file)}: ${e.message}`)
    continue
  }
  if (!ext) continue
  let ast
  try {
    ast = parseCode(ext.code)
  } catch (e) {
    console.log(`[语法错误] ${path.relative(SRC, file)}: ${e.message}`)
    bad++
    continue
  }
  const bindings = collectTopLevelTDZBindings(ast.program)
  if (!bindings.size) continue

  // 只检查顶层语句的同步执行路径
  const refs = []
  for (const stmt of ast.program.body) {
    if (stmt.type === 'ImportDeclaration' || stmt.type === 'FunctionDeclaration') continue
    collectSyncRefs(stmt, bindings, refs, false, null, null)
  }

  for (const r of refs) {
    const declStart = bindings.get(r.name)
    if (r.start < declStart) {
      // .vue 需要把偏移量加回去才能对上源文件行号
      const refLine = lineOf(ext.code, r.start) + (file.endsWith('.vue') ? lineOf(readFileSync(file, 'utf8'), ext.offset) - 1 : 0)
      const declLine = lineOf(ext.code, declStart) + (file.endsWith('.vue') ? lineOf(readFileSync(file, 'utf8'), ext.offset) - 1 : 0)
      console.log(
        `[TDZ 致命] ${path.relative(SRC, file)}  变量 "${r.name}" 在第 ${refLine} 行被使用，但声明在第 ${declLine} 行`,
      )
      bad++
    }
  }
}

console.log(`\n扫描文件数：${files.length}`)
console.log(bad === 0 ? 'TDZ_SCAN_PASS：未发现声明前使用的致命错误' : `TDZ_SCAN_FAIL：发现 ${bad} 处问题`)
process.exit(bad === 0 ? 0 : 1)
