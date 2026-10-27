// Every place the GUI reaches the backend API, resolved to (method, URL pattern).
//
// Usage: node scripts/api-bindings.mjs [srcRoot]   (default: ../src)
// Prints {"bindings": [...], "problems": [...]} as JSON. Read by
// pypsa-gui/backend/smoke/assistant_route_parity.py; fails closed: any request
// whose URL or method cannot be resolved statically is a problem, never skipped.
//
// Uses the TypeScript checker, so scoping, imports and local shadowing (a local
// `fetch` helper is not the global fetch) follow the real program.
import { createRequire } from 'node:module'
import { readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const ts = createRequire(join(here, '..', 'package.json'))('typescript')

const SKIP = ['.test.', '.spec.', '__fixtures__', 'test-utils', `${sep}test${sep}`, '.d.ts']
const METHODS = new Set(['get', 'post', 'put', 'patch', 'delete'])
const PARAM_WRAPPERS = new Set(['encodeURIComponent', 'encodeURI', 'String', 'Number'])
const URL_SINK_ATTRS = new Set(['href', 'src', 'action'])

class Unresolvable extends Error {}

function walkFiles(dir, out = []) {
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walkFiles(path, out)
    else if (/\.tsx?$/.test(name) && !SKIP.some(s => path.includes(s))) out.push(path)
  }
  return out
}

export function scan(srcRoot) {
  const files = walkFiles(srcRoot)
  const program = ts.createProgram(files, {
    allowJs: false, jsx: ts.JsxEmit.Preserve, noEmit: true, skipLibCheck: true,
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'],
  })
  const checker = program.getTypeChecker()
  const clientFile = resolve(srcRoot, 'api', 'client.ts')
  const bindings = []
  const problems = []
  const visited = new Set()
  const unused = new Set()  // URL helpers nothing calls: not a binding, listed for review
  const rel = sf => relative(srcRoot, sf.fileName).split(sep).join('/')
  const text = node => node.getText().replace(/\s+/g, ' ').slice(0, 160)

  const declOf = node => {
    let sym = checker.getSymbolAtLocation(node)
    if (sym && sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym)
    return sym?.declarations?.[0]
  }
  const isGlobal = decl => !decl || decl.getSourceFile().isDeclarationFile
  const unwrap = e => {
    while (ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isNonNullExpression(e)
      || ts.isSatisfiesExpression?.(e) || ts.isTypeAssertionExpression(e)) e = e.expression
    return e
  }
  const product = (a, b) => a.flatMap(x => b.map(y => x + y))

  // A function's single returned expression, or null.
  function returned(fn) {
    if (!fn.body) return null
    if (!ts.isBlock(fn.body)) return fn.body
    const rets = []
    const visit = n => {
      if (ts.isReturnStatement(n)) rets.push(n)
      else if (!ts.isFunctionLike(n)) ts.forEachChild(n, visit)
    }
    ts.forEachChild(fn.body, visit)
    return rets.length === 1 && rets[0].expression ? rets[0].expression : null
  }

  // The value expression a declaration binds, or null (parameters, destructuring, ...).
  function valueOf(decl) {
    if (!decl) return null
    if (ts.isVariableDeclaration(decl) && ts.isIdentifier(decl.name)) {
      const flags = ts.getCombinedNodeFlags(decl)
      return flags & ts.NodeFlags.Const ? decl.initializer ?? null : null
    }
    if (ts.isPropertyAssignment(decl)) return decl.initializer
    if (ts.isFunctionDeclaration(decl) || ts.isMethodDeclaration(decl)) return decl
    return null
  }

  // Every call site of each function declaration, for parameter propagation.
  let callIndex = null
  function callsOf(fn) {
    if (!callIndex) {
      callIndex = new Map()
      for (const sf of program.getSourceFiles()) {
        if (sf.isDeclarationFile || !files.includes(sf.fileName)) continue
        const visit = n => {
          if (ts.isCallExpression(n)) {
            const f = callee(n)
            if (f) callIndex.set(f, [...(callIndex.get(f) ?? []), n])
          }
          ts.forEachChild(n, visit)
        }
        visit(sf)
      }
    }
    return callIndex.get(fn) ?? []
  }

  // A URL parameter of a function: the union over its call sites.
  function fromParameter(decl, depth) {
    if (!decl || !ts.isParameter(decl) || !ts.isIdentifier(decl.name)) return null
    const fn = decl.parent
    const index = fn.parameters.indexOf(decl)
    const calls = callsOf(fn)
    if (!calls.length) return null
    return calls.flatMap(c => {
      if (!c.arguments[index]) throw new Unresolvable(`call passes no URL: ${text(c)}`)
      return urls(c.arguments[index], depth + 1)
    })
  }

  function callee(call) {
    const decl = declOf(ts.isPropertyAccessExpression(call.expression) ? call.expression.name : call.expression)
    let fn = valueOf(decl)
    if (fn) fn = unwrap(fn)
    return fn && ts.isFunctionLike(fn) ? fn : null
  }

  // Resolve an expression to URL alternatives; `{}` marks a parameter.
  function urls(expr, depth = 0) {
    expr = unwrap(expr)
    if (depth > 8) throw new Unresolvable(`helper recursion too deep: ${text(expr)}`)
    if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) {
      visited.add(expr)
      return [expr.text]
    }
    if (ts.isTemplateExpression(expr)) {
      visited.add(expr)
      let out = [expr.head.text]
      for (const span of expr.templateSpans) {
        const inQuery = out.every(u => u.includes('?'))
        if (!inQuery) out = product(out, interp(span.expression, depth))
        out = product(out, [span.literal.text])
      }
      return out
    }
    if (ts.isBinaryExpression(expr) && expr.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = urls(expr.left, depth + 1)
      if (left.every(u => u.includes('?'))) return left
      return product(left, interp(expr.right, depth))
    }
    if (ts.isConditionalExpression(expr)) {
      return [...urls(expr.whenTrue, depth + 1), ...urls(expr.whenFalse, depth + 1)]
    }
    if (ts.isIdentifier(expr) || ts.isPropertyAccessExpression(expr)) {
      const decl = declOf(ts.isPropertyAccessExpression(expr) ? expr.name : expr)
      const value = valueOf(decl)
      if (value && !ts.isFunctionLike(value)) return urls(value, depth + 1)
      const viaCalls = fromParameter(decl, depth)
      if (viaCalls) return viaCalls
    }
    if (ts.isCallExpression(expr)) {
      const fn = callee(expr)
      const body = fn && returned(fn)
      if (body) {
        visited.add(expr)
        return urls(body, depth + 1)
      }
    }
    throw new Unresolvable(`cannot resolve URL expression ${text(expr)}`)
  }

  // encodeURIComponent and friends, directly or through a const alias.
  function isParamWrapper(ex) {
    for (let i = 0; i < 4 && ex && ts.isIdentifier(ex); i++) {
      const decl = declOf(ex)
      if (PARAM_WRAPPERS.has(ex.text) && isGlobal(decl)) return true
      const value = valueOf(decl)
      ex = value && unwrap(value)
    }
    return false
  }

  // An interpolated part: a resolvable helper, else a parameter.
  function interp(expr, depth) {
    expr = unwrap(expr)
    if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr) || ts.isTemplateExpression(expr)
      || ts.isConditionalExpression(expr) || ts.isBinaryExpression(expr)) return urls(expr, depth + 1)
    if (ts.isIdentifier(expr) || ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) {
      const target = ts.isPropertyAccessExpression(expr) ? expr.name : expr
      const value = ts.isElementAccessExpression(expr) ? null : valueOf(declOf(target))
      if (value && !ts.isFunctionLike(value)) {
        try { return urls(value, depth + 1) } catch { return ['{}'] }
      }
      return ['{}']
    }
    if (ts.isCallExpression(expr)) {
      if (isParamWrapper(expr.expression)) return ['{}']
      const fn = callee(expr)
      const body = fn && returned(fn)
      if (body) return interp(body, depth + 1)
    }
    throw new Unresolvable(`cannot resolve URL interpolation \${${text(expr)}}`)
  }

  function normalize(url, viaClient) {
    url = url.split('?')[0].split('#')[0]
    if (viaClient && !url.startsWith('/api')) url = '/api' + (url.startsWith('/') ? url : '/' + url)
    if (url !== '/api' && !url.startsWith('/api/')) throw new Unresolvable(`not an /api/ URL: ${url}`)
    url = url.replace(/\/{2,}/g, '/').replace(/\/+$/, '')
    return url || '/'
  }

  function fetchMethod(opts) {
    if (!opts) return 'GET'
    let obj = unwrap(opts)
    if (ts.isIdentifier(obj)) obj = valueOf(declOf(obj)) && unwrap(valueOf(declOf(obj)))
    if (!obj || !ts.isObjectLiteralExpression(obj)) throw new Unresolvable(`fetch options are not an object literal: ${text(opts)}`)
    for (const p of obj.properties) {
      if (ts.isSpreadAssignment(p)) throw new Unresolvable(`fetch options spread another object: ${text(p)}`)
      if (p.name && p.name.getText() === 'method') {
        if (!ts.isPropertyAssignment(p)) throw new Unresolvable(`fetch method is not a literal: ${text(p)}`)
        const v = urls_literal(p.initializer)
        if (!v) throw new Unresolvable(`fetch method is not a literal: ${text(p.initializer)}`)
        return v.toUpperCase()
      }
    }
    return 'GET'
  }
  const urls_literal = e => {
    e = unwrap(e)
    return ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e) ? e.text : null
  }

  function record(sf, node, kind, method, urlExpr, viaClient) {
    try {
      const m = typeof method === 'function' ? method() : method
      for (const u of new Set(urls(urlExpr))) {
        bindings.push({ file: rel(sf), kind, method: m, url: normalize(u, viaClient) })
      }
    } catch (err) {
      if (!(err instanceof Unresolvable)) throw err
      problems.push({ file: rel(sf), call: text(node), reason: err.message })
    }
  }

  const isClient = expr => {
    const decl = declOf(expr)
    return decl && resolve(decl.getSourceFile().fileName) === clientFile
      && ts.isVariableDeclaration(decl) && decl.name.getText() === 'client'
  }

  for (const sf of program.getSourceFiles()) {
    if (sf.isDeclarationFile || !files.includes(sf.fileName)) continue
    const visit = node => {
      if (ts.isCallExpression(node)) {
        const ex = node.expression
        if (ts.isPropertyAccessExpression(ex) && isClient(ex.expression)) {
          const name = ex.name.text
          if (METHODS.has(name)) {
            if (node.arguments.length) record(sf, node, 'client', name.toUpperCase(), node.arguments[0], true)
            else problems.push({ file: rel(sf), call: text(node), reason: 'call without URL' })
          } else if (['request', 'getUri', 'head', 'options', 'postForm', 'putForm', 'patchForm'].includes(name)) {
            problems.push({ file: rel(sf), call: text(node), reason: 'unsupported HTTP entry point' })
          }
        } else if (ts.isIdentifier(ex) && ex.text === 'fetch' && isGlobal(declOf(ex))) {
          if (node.arguments.length) record(sf, node, 'fetch', () => fetchMethod(node.arguments[1]), node.arguments[0], false)
        } else if (ts.isPropertyAccessExpression(ex) && ex.name.text === 'sendBeacon') {
          record(sf, node, 'beacon', 'POST', node.arguments[0], false)
        } else if (ts.isPropertyAccessExpression(ex) && /^(window|location)$/.test(ex.expression.getText())
          && ['open', 'assign', 'replace'].includes(ex.name.text) && node.arguments.length) {
          record(sf, node, 'navigation', 'GET', node.arguments[0], false)
        } else if ((ts.isIdentifier(ex) && ex.text === 'axios')
          || (ts.isPropertyAccessExpression(ex) && ex.expression.getText() === 'axios' && ex.name.text !== 'isAxiosError'
            && !(ex.name.text === 'create' && resolve(sf.fileName) === clientFile))
          || (ts.isIdentifier(ex) && isClient(ex))) {
          problems.push({ file: rel(sf), call: text(node), reason: 'unsupported HTTP entry point' })
        }
      } else if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)) {
        if (node.expression.text === 'EventSource' && node.arguments?.length) {
          record(sf, node, 'event-source', 'GET', node.arguments[0], false)
        } else if (node.expression.text === 'XMLHttpRequest' || node.expression.text === 'WebSocket') {
          problems.push({ file: rel(sf), call: text(node), reason: 'unsupported HTTP entry point' })
        }
      } else if (ts.isJsxAttribute(node) && URL_SINK_ATTRS.has(node.name.getText()) && node.initializer) {
        const init = ts.isJsxExpression(node.initializer) ? node.initializer.expression : node.initializer
        if (init) sink(sf, node, init)
      } else if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
        && ts.isPropertyAccessExpression(node.left) && URL_SINK_ATTRS.has(node.left.name.text)) {
        sink(sf, node, node.right)
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }

  // href/src sinks: only URLs that resolve to /api/ count; blob:/external URLs do not.
  function sink(sf, node, expr) {
    try {
      for (const u of new Set(urls(expr))) {
        const path = u.split('?')[0]
        if (path === '/api' || path.startsWith('/api/')) {
          bindings.push({ file: rel(sf), kind: 'url', method: 'GET', url: normalize(u, false) })
        }
      }
    } catch (err) {
      if (!(err instanceof Unresolvable)) throw err
      // A sink that is not a static URL (blob: object URLs, props) is not an API binding.
    }
  }

  const sources = program.getSourceFiles().filter(sf => !sf.isDeclarationFile && files.includes(sf.fileName))
  const resolvesToApi = node => {
    try {
      const before = new Set(visited)
      const out = urls(node).some(u => /^\/api(\/|\?|$)/.test(u))
      visited.clear(); before.forEach(v => visited.add(v))
      return out
    } catch (err) {
      if (!(err instanceof Unresolvable)) throw err
      return false
    }
  }

  // A URL helper called outside any request (handed to a link, prop or
  // download): the GUI GETs that URL.
  for (const sf of sources) {
    const visit = node => {
      if (ts.isCallExpression(node) && !visited.has(node)) {
        const fn = callee(node)
        const body = fn && returned(fn)
        if (body && resolvesToApi(node)) record(sf, node, 'url', 'GET', node, false)
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }

  // Any /api/ URL expression no request, sink or helper call reached is unattributed (V1).
  for (const sf of sources) {
    const visit = node => {
      const candidate = ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
        ? /^\/api(\/|$)/.test(node.text)
        : ts.isTemplateExpression(node) && !visited.has(node) && resolvesToApi(node)
      if (candidate && !visited.has(node) && !ts.isImportDeclaration(node.parent)
        && !ts.isExportDeclaration(node.parent) && !insideHelper(node)) {
        problems.push({ file: rel(sf), call: text(node), reason: 'unattributed /api/ URL literal' })
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }

  // A literal inside a URL helper body that some call site resolved.
  function insideHelper(node) {
    for (let n = node.parent; n; n = n.parent) {
      if (visited.has(n)) return true
      if (ts.isFunctionLike(n)) {
        const body = returned(n)
        const inBody = !!body && node.pos >= body.pos && node.end <= body.end
        if (inBody && !callsOf(n).length) unused.add(`${rel(n.getSourceFile())}: ${text(node)}`)
        return inBody && (!callsOf(n).length || callsOf(n).some(c => visited.has(c)))
      }
    }
    return false
  }

  const key = o => JSON.stringify(Object.values(o))
  const uniq = arr => [...new Map(arr.map(o => [key(o), o])).values()].sort((a, b) => (key(a) < key(b) ? -1 : 1))
  return { bindings: uniq(bindings), problems: uniq(problems), unused_url_helpers: [...unused].sort() }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = resolve(process.argv[2] ?? join(here, '..', 'src'))
  process.stdout.write(JSON.stringify(scan(root)) + '\n')
}
