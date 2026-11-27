// UI copy guard: the words the user reads carry no internal ids and no raw
// model names. The UX reviewer blocked on the ids by hand on #442, #496 and
// #509; RAW-NAMES-1 (expert-catalogue spec §7.2, template-e2e gap 05) adds the
// model names, template component ids and Library ids.
//
// It scans the source, not a rendered tree: every JSX text, every copy
// attribute (title, aria-label, placeholder, ...) and every string literal of
// two or more words in every .ts/.tsx file under src/, except the test files
// and fixtures listed in EXCLUDED. Words that look like an id but are intended,
// or are waiting on a slice, are rows of uiCopyGuard.allow.json (shrink-only).
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(__dirname)
const REPO = path.resolve(SRC, '../../..')
const BACKEND = path.resolve(SRC, '../../backend')

/**
 * The only files under src/ the guard does not read: tests, fixtures and
 * test helpers. `*` matches within a directory, `**` across them. Pinned:
 * adding a glob widens what reaches the user unchecked.
 */
export const EXCLUDED = [
  '**/*.test.ts',
  '**/*.test.tsx',
  '**/__fixtures__/**',
  'test/**',
  'test-utils/**',
  'catalogue/guideFixture.ts',
  'catalogue/testEntries.ts',
  'components/runMonitor/runFixtures.ts',
  'pages/hubDesign/testFixtures.ts',
]

/** JSX attributes whose value is copy the user reads. */
const COPY_ATTRS = new Set([
  'title', 'aria-label', 'aria-description', 'placeholder', 'alt', 'label',
  'tooltip', 'helperText', 'description', 'heading', 'text', 'message',
  // A card's meta line (CardShell, "100 MW · gas").
  'meta',
])

/** What must not reach the user. Each pattern is one kind of internal id. */
const RULES: { name: string; re: RegExp }[] = [
  { name: 'section sign (§)', re: /§/ },
  { name: 'workstream id (W3a)', re: /\bW\d+[a-z]?\b/ },
  { name: 'question id (Q17)', re: /\bQ\d+\b/ },
  { name: 'ticket id (LC-12, DC-05, GS-2)', re: /\b(?:LC-\d+|DC-?\d+|GS-)/ },
  // The breadcrumb separator is U+203A. A plain U+003E between words is
  // not; a comparison ("> 0", ">=") and an arrow (U+2192) are fine.
  { name: 'plain ">" as a breadcrumb separator', re: /\S > (?![\d=])\S/ },
  {
    name: 'raw PyPSA class path (Link ›)',
    re: /\b(?:Generator|Link|Store|StorageUnit|Line|Transformer|Bus|Load)\s?(?:›|>)/,
  },
  // "MC" for marginal cost (spec §7.2): in a data line ("· MC 40 €/MWh") or a
  // "Gen MC" column. Elsewhere "MC" is the Monte Carlo study ("MC seed", "MC LOLE").
  { name: 'abbreviated model name (MC, marginal cost)', re: /(?:·\s*|\bGen )MC\b/ },
]

// ---------------------------------------------------------------------------
// The names that must not reach the user (RAW-NAMES-1). Each source fails with
// a message naming its file when it is missing or its shape changed, and must
// load more names than its floor, so the rule cannot pass by reading nothing.
// ---------------------------------------------------------------------------

const MODEL_NAMES_FILE = 'catalogue/modelNames.generated.json'
const TEMPLATES_DIR = 'project_templates'
const LIBRARY_SOURCES = [
  'project_templates/*/price_series/meta.json → series[].name',
  'services/library/defaults_pack/versions/*/tariffs.json → tariffs[].tariff.id',
  'services/library/common_items/*/*.json → payload.id',
]
export const FLOORS = { modelNames: 100, snakeModelNames: 80, templateIds: 35, libraryIds: 8 }

function readJson(file: string): unknown {
  if (!fs.existsSync(file)) throw new Error(`copy guard: ${path.relative(REPO, file)} is missing`)
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

/** Every model name of the Parameter catalogue: class → {model_name: plain name}. */
export function modelNames(): string[] {
  const data = readJson(path.join(SRC, MODEL_NAMES_FILE))
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error(`copy guard: ${MODEL_NAMES_FILE} is no longer an object of class → {model_name: plain name}`)
  }
  const names = new Set<string>()
  for (const [cls, byName] of Object.entries(data as Record<string, unknown>)) {
    if (!byName || typeof byName !== 'object') throw new Error(`copy guard: ${MODEL_NAMES_FILE} class "${cls}" holds no name map`)
    for (const n of Object.keys(byName)) names.add(n)
  }
  return [...names].sort()
}

/**
 * Template component ids with an underscore: the names `n.add("Class", "<id>")`
 * gives in the backend's template builders. A one-word id ("grid", "battery")
 * is a plain word in copy and is left alone.
 */
export function templateIds(): string[] {
  const dir = path.join(BACKEND, TEMPLATES_DIR)
  if (!fs.existsSync(dir)) throw new Error(`copy guard: backend/${TEMPLATES_DIR} is missing`)
  const ids = new Set<string>()
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith('.py'))) {
    const text = fs.readFileSync(path.join(dir, f), 'utf8')
    for (const m of text.matchAll(/\.add\(\s*"[A-Z]\w*",\s*"([a-z][a-z0-9]*_[a-z0-9_]*)"/g)) ids.add(m[1])
  }
  return [...ids].sort()
}

/** Library item ids: price series names and tariff ids the Library ships. */
export function libraryIds(): string[] {
  const ids = new Set<string>()
  const need = (v: unknown, where: string): string => {
    if (typeof v !== 'string' || !v) throw new Error(`copy guard: ${where} has no id (renamed key?)`)
    return v
  }
  const tpl = path.join(BACKEND, 'project_templates')
  for (const t of fs.readdirSync(tpl)) {
    const meta = path.join(tpl, t, 'price_series', 'meta.json')
    if (!fs.existsSync(meta)) continue
    const series = (readJson(meta) as { series?: { name?: unknown }[] }).series
    if (!Array.isArray(series)) throw new Error(`copy guard: backend/project_templates/${t}/price_series/meta.json has no "series" list`)
    for (const s of series) ids.add(need(s.name, `backend/project_templates/${t}/price_series/meta.json series[].name`))
  }
  const versions = path.join(BACKEND, 'services/library/defaults_pack/versions')
  for (const v of fs.readdirSync(versions)) {
    const file = path.join(versions, v, 'tariffs.json')
    if (!fs.existsSync(file)) continue
    const tariffs = (readJson(file) as { tariffs?: { tariff?: { id?: unknown } }[] }).tariffs
    if (!Array.isArray(tariffs)) throw new Error(`copy guard: defaults pack ${v}/tariffs.json has no "tariffs" list`)
    for (const t of tariffs) ids.add(need(t.tariff?.id, `defaults pack ${v}/tariffs.json tariffs[].tariff.id`))
  }
  const common = path.join(BACKEND, 'services/library/common_items')
  for (const kind of fs.readdirSync(common)) {
    for (const f of fs.readdirSync(path.join(common, kind)).filter((f) => f.endsWith('.json'))) {
      const item = readJson(path.join(common, kind, f)) as { payload?: { id?: unknown } }
      ids.add(need(item.payload?.id, `services/library/common_items/${kind}/${f} payload.id`))
    }
  }
  return [...ids].sort()
}

function escapeRe(s: string): string {
  return s.replace(/[.+^${}()|[\]\\]/g, '\\$&')
}

const word = (alts: string[]) => alts.map(escapeRe).sort((a, b) => b.length - a.length).join('|')

/** Name rules, built from the three sources above. */
export function nameRules(names = { model: modelNames(), template: templateIds(), library: libraryIds() }) {
  const snake = names.model.filter((n) => n.includes('_'))
  const short = names.model.filter((n) => !n.includes('_'))
  const edge = '(?<![A-Za-z0-9_])'
  const end = '(?![A-Za-z0-9_])'
  return {
    // A snake_case model name anywhere, as a whole word.
    snake: new RegExp(`${edge}(?:${word(snake)})${end}`),
    // A short model name ("x", "bus") only quoted or as `name=`; inside <code> see shortInCode.
    // An HTML attribute (`x="4"`, `data-bus-name="`) or a CSS selector (`[type=hidden]`) is markup, not copy.
    short: new RegExp(`(?:["'\`“‘](?:${word(short)})["'\`”’])|(?<![A-Za-z0-9_[-])(?:${word(short)})=(?!["'])`),
    shortInCode: new RegExp(`${edge}(?:${word(short)})${end}`),
    // A template component id, also with a stage year ("bess_new_2027").
    template: new RegExp(`${edge}(?:${word(names.template)})(?:_\\d{4})?${end}`),
    library: new RegExp(`(?<![A-Za-z0-9_-])(?:${word(names.library)})(?![A-Za-z0-9_-])`),
  }
}

// ---------------------------------------------------------------------------
// The scan.
// ---------------------------------------------------------------------------

function globToRe(glob: string): RegExp {
  const braces = glob.replace(/\{([^}]+)\}/g, (_, alts: string) => `(${alts.split(',').map(escapeRe).join('|')})`)
  const body = braces
    .split(/(\*\*\/|\/\*\*$|\*|\(.*?\))/)
    .map((part) => {
      if (part === '**/') return '(?:.*/)?'
      if (part === '/**') return '/.*'
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
    return /\.tsx?$/.test(e.name) ? [p] : []
  })
}

/** Every file the guard reads, relative to src/ with forward slashes. */
export function scannedFiles(): string[] {
  const excluded = EXCLUDED.map(globToRe)
  return sourceFiles(SRC)
    .map((f) => path.relative(SRC, f).split(path.sep).join('/'))
    .filter((rel) => !excluded.some((re) => re.test(rel)))
    .sort()
}

/** The user-facing strings of one file, with the line each starts on. */
export function copyStrings(file: string, text: string): { line: number; text: string; inCode: boolean }[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const out: { line: number; text: string; inCode: boolean }[] = []
  const inCode = (node: ts.Node): boolean => {
    for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
      if (ts.isJsxElement(p)) return p.openingElement.tagName.getText(sf) === 'code'
    }
    return false
  }
  const add = (node: ts.Node, value: string) => {
    if (value.trim()) out.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, text: value, inCode: inCode(node) })
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

/** One row of uiCopyGuard.allow.json. */
export type AllowRow = { file: string; text: string; surface: string; reason: string; fix: string }

export const ALLOW: AllowRow[] = JSON.parse(fs.readFileSync(path.join(SRC, 'uiCopyGuard.allow.json'), 'utf8'))

let cachedRules: ReturnType<typeof nameRules> | null = null
const rules = () => (cachedRules ??= nameRules())

export function findings(file: string, text: string, allow: AllowRow[] = ALLOW, names = rules()): string[] {
  const hits: string[] = []
  const nameChecks: { name: string; re: RegExp; codeOnly?: boolean }[] = [
    { name: 'model name', re: names.snake },
    { name: 'model name (quoted or name=)', re: names.short },
    { name: 'model name (in <code>)', re: names.shortInCode, codeOnly: true },
    { name: 'template component id', re: names.template },
    { name: 'Library id', re: names.library },
  ]
  for (const s of copyStrings(file, text)) {
    const scrubbed = allow.filter((a) => a.file === file).reduce((t, a) => t.split(a.text).join(''), s.text)
    for (const rule of [...RULES, ...nameChecks]) {
      if ('codeOnly' in rule && rule.codeOnly && !s.inCode) continue
      const m = rule.re.exec(scrubbed)
      if (m) hits.push(`${file}:${s.line}  ${rule.name}: "${m[0]}" in "${s.text.trim().slice(0, 80)}"`)
    }
  }
  return hits
}

const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8')

/**
 * The allow rows, pinned as an exact key set. A new row needs this list
 * changed too, in review; a row removed from the JSON is removed here.
 */
const PINNED_ALLOW_KEYS = [
  'layout/PropertiesPanel.tsx|MC',
  'layout/PropertiesPanel.tsx|p_set',
  'layout/PropertiesPanel.tsx|p_nom',
  'layout/PropertiesPanel.tsx|e_nom',
  'layout/PropertiesPanel.tsx|s_nom',
  'layout/PropertiesPanel.tsx|v_nom',
  'layout/properties/cardKit.tsx|grid_import',
  'pages/SolverSettings.tsx|p_nom',
  'pages/SolverSettings.tsx|committable=',
  'pages/SolverSettings.tsx|co2_emissions',
  'pages/SolverSettings.tsx|carrier',
  'pages/SolverSettings.tsx|marginal_cost',
  'pages/SolverSettings.tsx|sub_network',
  'pages/SolverSettings.tsx|p_set',
  'pages/SolverSettings.tsx|p_max_pu',
  'pages/hubDesign/delegate.ts|grid_import',
  'pages/hubDesign/delegate.ts|outage_rate_value',
  'pages/hubDesign/delegate.ts|mttr_hours',
  'pages/hubDesign/delegate.ts|eh_role',
  'pages/hubDesign/delegate.ts|eh_poc',
  'pages/hubDesign/delegate.ts|eh_critical',
  'pages/hubDesign/delegate.ts|eh_sk_mva',
  'pages/modelHorizon/StepPeriodEconomics.tsx|discount_rate',
  'pages/modelHorizon/StepPeriodEconomics.tsx|overnight_cost',
  'pages/modelHorizon/StepPeriodEconomics.tsx|p_nom',
  'pages/modelHorizon/StepPeriodEconomics.tsx|build_year',
  'pages/results/AggregatedOverview.tsx|marginal_cost',
  'pages/results/CapacityExpansion.tsx|overnight_cost',
  'pages/results/CapacityExpansion.tsx|build_year',
  'pages/results/CapacityExpansion.tsx|capital_cost',
  'pages/results/Curtailment.tsx|p_max_pu',
  'pages/results/Economics.tsx|marginal_cost',
  'pages/results/Economics.tsx|capital_cost',
  'pages/results/Economics.tsx|fom_cost',
  'pages/results/Economics.tsx|overnight_cost',
  'pages/results/LoadFlow.tsx|co2_emissions',
  'pages/results/LoadFlow.tsx|s_nom',
  'pages/results/LoadFlow.tsx|p_nom',
  'pages/results/LoadFlow.tsx|start_up_cost',
  'pages/results/LoadFlow.tsx|shut_down_cost',
  'pages/results/LoadFlow.tsx|min_up_time',
  'pages/results/LoadFlow.tsx|v_nom',
  'pages/results/LoadFlow.tsx|curtailment_cost',
  'pages/results/LoadFlow.tsx|MC',
  'pages/results/StorageCycling.tsx|max_hours',
  'utils/decisionVocabulary.ts|p_nom',
  'utils/decisionVocabulary.ts|max_hours',
  'utils/decisionVocabulary.ts|efficiency_store',
  'utils/decisionVocabulary.ts|efficiency_dispatch',
  'utils/decisionVocabulary.ts|grid_import',
  'utils/decisionVocabulary.ts|p_set',
  'utils/decisionVocabulary.ts|eh_role',
]

describe('UI copy guard: what it reads', () => {
  it('scans every .ts/.tsx under src/ except the pinned exclusions (exact set, against a filesystem walk)', () => {
    const walked = (fs.readdirSync(SRC, { recursive: true }) as string[])
      .map((f) => f.split(path.sep).join('/'))
      .filter((f) => /\.tsx?$/.test(f) && !f.startsWith('node_modules/'))
      .filter((f) => !/\.test\.tsx?$/.test(f))
      .filter((f) => !f.includes('/__fixtures__/') && !f.startsWith('test/') && !f.startsWith('test-utils/'))
      .filter((f) => !['catalogue/guideFixture.ts', 'catalogue/testEntries.ts',
        'components/runMonitor/runFixtures.ts', 'pages/hubDesign/testFixtures.ts'].includes(f))
      .sort()
    expect(scannedFiles()).toEqual(walked)
    expect(scannedFiles().length).toBeGreaterThan(600)
    // catalogue/ is read too (U1, E1); only its fixtures are not.
    expect(scannedFiles()).toContain('catalogue/GuideDrawer.tsx')
    expect(scannedFiles()).toContain('catalogue/searchIndex.ts')
  })

  it('pins the exclusions as an exact list of globs', () => {
    expect(EXCLUDED).toEqual([
      '**/*.test.ts', '**/*.test.tsx', '**/__fixtures__/**', 'test/**', 'test-utils/**',
      'catalogue/guideFixture.ts', 'catalogue/testEntries.ts',
      'components/runMonitor/runFixtures.ts', 'pages/hubDesign/testFixtures.ts',
    ])
  })

  it('loads each name source above its floor', () => {
    const model = modelNames()
    expect(model.length).toBeGreaterThan(FLOORS.modelNames)
    expect(model.filter((n) => n.includes('_')).length).toBeGreaterThan(FLOORS.snakeModelNames)
    expect(model).toEqual(expect.arrayContaining(['p_nom', 'p_nom_extendable', 'marginal_cost', 'x', 'bus']))
    const template = templateIds()
    expect(template.length).toBeGreaterThan(FLOORS.templateIds)
    expect(template).toEqual(expect.arrayContaining(['it_load', 'genset_new', 'bess_new', 'grid_import', 'dc_mv', 'site_transformer', 'hospital_load']))
    const library = libraryIds()
    expect(library.length).toBeGreaterThan(FLOORS.libraryIds)
    expect(library).toEqual(expect.arrayContaining(['ipp_da_base', 'ipp_da_cannibalised']))
    expect(LIBRARY_SOURCES).toHaveLength(3)
  })

  it('names the missing file when a source is gone', () => {
    expect(() => readJson(path.join(SRC, 'catalogue/nope.generated.json'))).toThrow(/catalogue\/nope\.generated\.json is missing/)
  })
})

describe('UI copy guard: the rules', () => {
  const names = nameRules({
    model: ['p_nom', 'p_nom_extendable', 'marginal_cost', 'x', 'bus'],
    template: ['genset_new', 'it_load'],
    library: ['ipp_da_base'],
  })
  const hits = (sample: string) => findings('sample.tsx', sample, [], names).map((h) => h.split('  ')[1].split(' in ')[0])

  it('finds each kind of internal id in a sample', () => {
    const sample = [
      'export const a = <p title="See §4">Ready for W3a</p>',
      'export const b = "Answer Q17 first"',
      'export const c = `Tracked as LC-12 today`',
      'export const d = <span>Link › grid</span>',
      'export const e = <span>Generator > wind</span>',
      'export const f = "Connection & grid > Link grid"',
    ].join('\n')
    expect(hits(sample)).toEqual([
      'section sign (§): "§"',
      'workstream id (W3a): "W3a"',
      'question id (Q17): "Q17"',
      'ticket id (LC-12, DC-05, GS-2): "LC-12"',
      'raw PyPSA class path (Link ›): "Link ›"',
      'plain ">" as a breadcrumb separator: "r > w"',
      'raw PyPSA class path (Link ›): "Generator >"',
      'plain ">" as a breadcrumb separator: "d > L"',
    ])
  })

  it('finds a model name in each copy channel: JSX text, every copy attribute, a template literal', () => {
    expect(hits('export const a = <p>Set p_nom_extendable first</p>')).toEqual(['model name: "p_nom_extendable"'])
    for (const attr of COPY_ATTRS) {
      expect(hits(`export const a = <p ${attr}="p_nom"/>`), attr).toEqual(['model name: "p_nom"'])
    }
    expect(hits('export const a = (v: number) => `Raise marginal_cost to ${v}`')).toEqual(['model name: "marginal_cost"'])
    expect(hits('export const a = "Sized by the optimiser as p_nom"')).toEqual(['model name: "p_nom"'])
  })

  it('finds a short model name only quoted, as name= or inside <code>', () => {
    expect(hits('export const a = "Set “bus” first"')).toEqual(['model name (quoted or name=): "“bus”"'])
    expect(hits('export const a = "Needs x=0.1 here"')).toEqual(['model name (quoted or name=): "x="'])
    expect(hits('export const a = <p>Set <code>x</code> first</p>')).toEqual(['model name (in <code>): "x"'])
    expect(hits('export const a = <p>Pick the bus it connects to, x or y</p>')).toEqual([])
  })

  it('finds template component ids (with a stage year) and Library ids', () => {
    expect(hits('export const a = "Size genset_new first"')).toEqual(['template component id: "genset_new"'])
    expect(hits('export const a = "Size genset_new_2027 first"')).toEqual(['template component id: "genset_new_2027"'])
    expect(hits('export const a = "Uses ipp_da_base v1"')).toEqual(['Library id: "ipp_da_base"'])
    expect(hits('export const a = `100 MW · MC ${3} €/MWh`')).toEqual(['abbreviated model name (MC, marginal cost): "· MC"'])
    expect(hits('export const a = <CardShell meta={`100 MW · MC ${3} €/MWh`} />')).toEqual(['abbreviated model name (MC, marginal cost): "· MC"'])
    expect(hits('export const a = <th>Gen MC</th>')).toEqual(['abbreviated model name (MC, marginal cost): "Gen MC"'])
  })

  it('leaves each ignored kind alone: imports, type literals, code attributes, one-word literals, identifiers, longer names', () => {
    const sample = [
      'import { Link } from "react-router-dom"',
      'import type { p_nom } from "./p_nom types"',
      'export const a = <Link to="/x" className="W3a Q17 p_nom" data-testid="hub-rail-step-W3a p_nom">Open</Link>',
      'const b: "Link" | "Bus" | "p_nom extendable" = "Bus"',
      'const c = a > b ? Link : Bus',
      'export const d = "Needs a price > 0 and a size >= 5"',
      'export const e = "Connection & grid › Link grid"',
      'export const f = "Grid → Link grid"',
      'export const g = { p_nom: 1, field: "p_nom", key: "marginal_cost" }',
      'export const h = "the p_nom_max limit"',
      'export const i = `<text x="${1}" y="4">`',
      'export const j = "input:not([type=hidden]), select"',
      'export const k = `<div data-bus-name="${1}" class="a b">`',
      'export const l = "MC draws per batch, the sequential MC and its MC LOLE"',
    ].join('\n')
    // "p_nom_max" is not in this sample's names, and "p_nom" inside it is not a whole word.
    expect(hits(sample)).toEqual([])
  })
})

describe('UI copy guard: the allowlist', () => {
  it('every row has a file, exact text, a surface, a reason and a fix that exists in the repo', () => {
    for (const row of ALLOW) {
      expect(Object.keys(row).sort(), JSON.stringify(row)).toEqual(['file', 'fix', 'reason', 'surface', 'text'])
      for (const k of ['file', 'text', 'surface', 'reason', 'fix'] as const) expect(row[k].trim(), `${k} of ${row.file}`).not.toBe('')
      expect(fs.existsSync(path.join(SRC, row.file)), row.file).toBe(true)
      expect(row.fix, `fix of ${row.file}`).toMatch(/^\.scratch\/[^/]+\/issues\/[^/]+\.md$/)
      expect(fs.existsSync(path.join(REPO, row.fix)), `fix of ${row.file}: ${row.fix}`).toBe(true)
      expect(row.surface, row.file).toMatch(/\b(Guided|Expert)\b/)
    }
  })

  it('the rows are the pinned key set', () => {
    expect(ALLOW.map((r) => `${r.file}|${r.text}`).sort()).toEqual([...PINNED_ALLOW_KEYS].sort())
  })

  it('no row is stale: each still hides a finding', () => {
    for (const row of ALLOW) {
      const text = read(row.file)
      const without = findings(row.file, text, ALLOW.filter((r) => r !== row))
      expect(without.length, `stale allow row ${row.file} "${row.text}"`).toBeGreaterThan(findings(row.file, text).length)
    }
  })

  it('the "MC" row spells out what MC stands for', () => {
    const mc = ALLOW.find((r) => r.text === 'MC')
    expect(mc?.reason).toMatch(/marginal cost/)
  })
})

describe('UI copy guard: the scan', () => {
  it('the user-facing copy of every file has no internal ids and no raw names', () => {
    const hits = scannedFiles().flatMap((rel) => findings(rel, read(rel)))
    expect(hits).toEqual([])
  })

  it('the two places that show a model name hold no model-name literal of their own', () => {
    for (const rel of ['catalogue/GuideDrawer.tsx', 'components/EquipmentTableImport.tsx']) {
      expect(ALLOW.filter((r) => r.file === rel)).toEqual([])
      expect(findings(rel, read(rel), [])).toEqual([])
    }
  })
})
