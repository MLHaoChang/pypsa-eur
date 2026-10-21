// UI copy guard: the words the user reads on the flow rail and sidebar, the
// Issues panel, the grid-readiness views and the scope cards carry no internal
// ids. The UX reviewer blocked on this by hand on #442, #496 and #509.
//
// It scans the source, not a rendered tree: every JSX text, every copy
// attribute (title, aria-label, placeholder, ...) and every string literal of
// two or more words in the files below. A surface whose files do not exist yet
// is skipped, so the guard covers a new directory the day it lands.
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(__dirname)

/** Surfaces the user reads. `*` matches within a directory, `**` across them. */
const SURFACES = [
  'components/gridReadiness/**/*.{ts,tsx}',
  'pages/IssuesPanel*.tsx',
  'layout/Sidebar.tsx',
  'layout/sidebar/*.tsx',
  'layout/flowSections.ts',
  'pages/hubDesign/StepRail.tsx',
  'pages/hubDesign/sectionLabels.ts',
  'pages/hubDesign/cards/*.tsx',
  'components/flowScope/**/*.{ts,tsx}',
  '**/*Scope*.tsx',
]

/** JSX attributes whose value is copy the user reads. */
const COPY_ATTRS = new Set([
  'title', 'aria-label', 'aria-description', 'placeholder', 'alt', 'label',
  'tooltip', 'helperText', 'description', 'heading', 'text', 'message',
])

/** What must not reach the user. Each pattern is one kind of internal id. */
const RULES: { name: string; re: RegExp }[] = [
  { name: 'section sign (§)', re: /§/ },
  { name: 'workstream id (W3a)', re: /\bW\d+[a-z]?\b/ },
  { name: 'question id (Q17)', re: /\bQ\d+\b/ },
  { name: 'ticket id (LC-12, DC-05, GS-2)', re: /\b(?:LC-\d+|DC-?\d+|GS-)/ },
  {
    name: 'raw PyPSA class path (Link ›)',
    re: /\b(?:Generator|Link|Store|StorageUnit|Line|Transformer|Bus|Load)\s?(?:›|>)/,
  },
]

/**
 * Intended words that look like an id. Each entry names the file and the exact
 * text it may contain, and why.
 */
const ALLOW: { file: string; text: string; why: string }[] = []

function escapeRe(s: string): string {
  return s.replace(/[.+^${}()|[\]\\]/g, '\\$&')
}

function globToRe(glob: string): RegExp {
  const braces = glob.replace(/\{([^}]+)\}/g, (_, alts: string) => `(${alts.split(',').map(escapeRe).join('|')})`)
  const body = braces
    .split(/(\*\*\/|\*|\(.*?\))/)
    .map((part) => {
      if (part === '**/') return '(?:.*/)?'
      if (part === '*') return '[^/]*'
      if (part.startsWith('(') && part.endsWith(')')) return part
      return escapeRe(part)
    })
    .join('')
  return new RegExp(`^${body}$`)
}

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) return e.name === 'node_modules' ? [] : sourceFiles(p)
    return /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : []
  })
}

export function surfaceFiles(): string[] {
  const res = SURFACES.map(globToRe)
  return sourceFiles(SRC)
    .map((f) => path.relative(SRC, f).split(path.sep).join('/'))
    .filter((rel) => res.some((re) => re.test(rel)))
    .sort()
}

/** The user-facing strings of one file, with the line each starts on. */
export function copyStrings(file: string, text: string): { line: number; text: string }[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const out: { line: number; text: string }[] = []
  const add = (node: ts.Node, value: string) => {
    if (value.trim()) out.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, text: value })
  }
  const inImportOrType = (node: ts.Node): boolean => {
    for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
      if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isLiteralTypeNode(p)) return true
      if (ts.isJsxAttribute(p)) return !COPY_ATTRS.has(p.name.getText(sf))
    }
    return false
  }
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) add(node, node.text)
    else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const attr = ts.isJsxAttribute(node.parent) ? node.parent.name.getText(sf) : null
      const isCopy = attr !== null ? COPY_ATTRS.has(attr) : /\s/.test(node.text.trim()) && !inImportOrType(node)
      if (isCopy) add(node, node.text)
    } else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      if (/\s/.test(node.text.trim()) && !inImportOrType(node)) add(node, node.text)
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

export function findings(file: string, text: string): string[] {
  const hits: string[] = []
  for (const s of copyStrings(file, text)) {
    const scrubbed = ALLOW.filter((a) => a.file === file).reduce((t, a) => t.split(a.text).join(''), s.text)
    for (const rule of RULES) {
      const m = rule.re.exec(scrubbed)
      if (m) hits.push(`${file}:${s.line}  ${rule.name}: "${m[0]}" in "${s.text.trim().slice(0, 80)}"`)
    }
  }
  return hits
}

describe('UI copy guard', () => {
  it('scans the surfaces that exist on master', () => {
    const files = surfaceFiles()
    expect(files).toContain('pages/IssuesPanel.tsx')
    expect(files).toContain('layout/Sidebar.tsx')
    expect(files).toContain('pages/hubDesign/StepRail.tsx')
    const strings = files.flatMap((rel) => copyStrings(rel, fs.readFileSync(path.join(SRC, rel), 'utf8')))
    expect(strings.length).toBeGreaterThan(100)
  })

  it('finds each kind of internal id in a sample', () => {
    const sample = [
      'export const a = <p title="See §4">Ready for W3a</p>',
      'export const b = "Answer Q17 first"',
      'export const c = `Tracked as LC-12 today`',
      'export const d = <span>Link › grid_import</span>',
      'export const e = <span>Generator > wind</span>',
    ].join('\n')
    expect(findings('sample.tsx', sample).map((h) => h.split('  ')[1].split(' in ')[0])).toEqual([
      'section sign (§): "§"',
      'workstream id (W3a): "W3a"',
      'question id (Q17): "Q17"',
      'ticket id (LC-12, DC-05, GS-2): "LC-12"',
      'raw PyPSA class path (Link ›): "Link ›"',
      'raw PyPSA class path (Link ›): "Generator >"',
    ])
  })

  it('leaves code identifiers, class names and test ids alone', () => {
    const sample = [
      'import { Link } from "react-router-dom"',
      'export const a = <Link to="/x" className="W3a Q17" data-testid="hub-rail-step-W3a">Open</Link>',
      'const b: "Link" | "Bus" = "Bus"',
      'const c = a > b ? Link : Bus',
    ].join('\n')
    expect(findings('sample.tsx', sample)).toEqual([])
  })

  it('the user-facing copy of every surface file has no internal ids', () => {
    const hits = surfaceFiles().flatMap((rel) => findings(rel, fs.readFileSync(path.join(SRC, rel), 'utf8')))
    expect(hits).toEqual([])
  })
})
