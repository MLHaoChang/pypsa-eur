// Geometry templates for the 3D site view's asset library (Phase 2 plan
// Task 1.3). A template turns one number — the asset's size driver (MW,
// MWh, MVA) — plus the numbers its library entry carries into parts: boxes
// and cylinders relative to the object origin, a footprint, a land take and
// the anchors the results layer animates (fill gauge, rotors, glow, flow).
//
// Every number and colour comes from the entry's `params`; this file holds
// the shapes. Pure: no React, no three.js.

export type Shape = 'box' | 'cylinder'
export type Axis = 'up' | 'east' | 'north'
export type AnchorKind = 'fill' | 'rotor' | 'emissive' | 'flow'

export interface Part {
  /** Centre of the part relative to the object origin, metres (east, north, up). */
  pos: [number, number, number]
  /**
   * Size (east extent, north extent, height), metres — the same order for a
   * box and a cylinder. A cylinder along `up` is [d, d, h]; along `north`
   * [d, L, d]; along `east` [L, d, d].
   */
  size: [number, number, number]
  /** Rotation about the vertical axis, radians, counter-clockwise from east. */
  rotZ?: number
  /** Rotation about the east axis, radians (tilted PV tables). */
  rotX?: number
  /**
   * Rotation about the north axis, radians (turbine blades in the rotor
   * plane): the part's east axis turns to (cos rotN, −sin rotN) in the
   * (east, up) plane (partGeometry.ts `partMatrix`).
   */
  rotN?: number
  /** Optional per-part colour override. */
  color?: string
  /** Default box. */
  shape?: Shape
  /** A cylinder's axis; default up. */
  axis?: Axis
  /** A unit a hero model can stand in for (a container, a turbine tower, a tank, a table, a hall). */
  heroable?: boolean
  /** The animation anchor this part belongs to. */
  anchor?: AnchorKind
  /** Rotor parts: which turbine of the object. */
  turbine?: number
}

export interface Anchors {
  /** Where the fill gauge lies: a slim bar across the top of the units; it fills along east. */
  fill?: { pos: [number, number, number]; size: [number, number, number] }
  /** One per drawn turbine: the hub, about which that turbine's rotor parts spin (axis: north). */
  rotors?: { turbine: number; hub: [number, number, number] }[]
  /** The body glows with output or load. */
  emissive?: boolean
  /** Power flows along this segment, owner (yard) side → far side. */
  flow?: { from: [number, number, number]; to: [number, number, number] }
}

export type TemplateId =
  | 'yard' | 'manifold' | 'transformer' | 'bay'
  | 'unitGrid' | 'tankArray' | 'turbineArray'
  | 'pvField' | 'pvRoof' | 'hall' | 'reservoir' | 'plant' | 'cube'

export type ParamValue = number | string | boolean
export interface Geometry {
  template: TemplateId
  params: Record<string, ParamValue>
}

export interface TemplateInput {
  /** The size driver (MW, MWh or MVA; the entry's size rule decides which). */
  amount: number
  /** Yards: number of bays (feeders, transformers) at the bus. */
  bays?: number
  /** Yards: nominal voltage, kV. */
  vNom?: number
  /**
   * Rooftop PV: the free part of a hall's roof (width, depth, the free
   * area's centre north of the hall's origin, the roof height). Absent =
   * its own canopy.
   */
  roof?: { w: number; d: number; cy: number; h: number }
}

export interface TemplateOutput {
  parts: Part[]
  footprint: [number, number]
  areaM2: number
  /** Real unit count (containers, turbines, tanks, tables). */
  count: number
  /** Units each drawn part stands for (> 1 beyond the draw cap). */
  each: number
  anchors: Anchors
  /** Rooftop PV: false when not even one table fits the roof (it stays on its canopy). */
  fitsRoof?: boolean
}

/** Which anchors each template provides (asserted by templates.test.ts). */
export const TEMPLATE_ANCHORS: Record<TemplateId, AnchorKind[]> = {
  yard: [], manifold: [], transformer: ['flow'], bay: ['flow'],
  unitGrid: [], tankArray: ['fill'], turbineArray: ['rotor'],
  pvField: ['emissive'], pvRoof: ['emissive'], hall: ['emissive'], reservoir: ['fill'], plant: ['emissive'], cube: ['fill'],
}

export const TEMPLATE_IDS = Object.keys(TEMPLATE_ANCHORS) as TemplateId[]

// ── helpers ─────────────────────────────────────────────────────────────────

/** Draw at most this many units per object; beyond it each drawn unit stands for several. */
export const MAX_UNITS = 120

const count = (amount: number, per: number): number => Math.max(1, Math.ceil(Math.max(amount, 0) / per))

function capped(n: number): { drawn: number; each: number } {
  if (n <= MAX_UNITS) return { drawn: n, each: 1 }
  const each = Math.ceil(n / MAX_UNITS)
  return { drawn: Math.ceil(n / each), each }
}

/** `n` identical units on a grid, `perRow` across, centred on the origin. */
function gridParts(n: number, size: [number, number, number], perRow: number, gap: number, extra: Omit<Part, 'pos' | 'size'> = {}): { parts: Part[]; footprint: [number, number] } {
  const cols = Math.min(n, perRow)
  const rows = Math.ceil(n / perRow)
  const pitchX = size[0] + gap
  const pitchY = size[1] + gap
  const w = cols * pitchX - gap
  const d = rows * pitchY - gap
  const parts: Part[] = []
  for (let i = 0; i < n; i++) {
    const c = i % perRow, r = Math.floor(i / perRow)
    parts.push({ ...extra, pos: [-w / 2 + size[0] / 2 + c * pitchX, -d / 2 + size[1] / 2 + r * pitchY, size[2] / 2], size: [...size] as [number, number, number] })
  }
  return { parts, footprint: [w, d] }
}

const num = (p: Record<string, ParamValue>, k: string): number => p[k] as number
const str = (p: Record<string, ParamValue>, k: string): string => p[k] as string
const opt = (p: Record<string, ParamValue>, k: string): string | undefined => (typeof p[k] === 'string' ? (p[k] as string) : undefined)

/** A slim gauge bar across the top of the units (inside the footprint; it fills along east). */
function gaugeAbove(w: number, h: number): Anchors['fill'] {
  return { pos: [0, 0, h + 1.2], size: [Math.max(w * 0.8, 2), 0.6, 0.6] }
}

// ── templates ───────────────────────────────────────────────────────────────

function yard(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const bays = input.bays ?? 0, vNom = input.vNom ?? 0
  const w = Math.max(num(p, 'minWidth'), num(p, 'baseWidth') + bays * num(p, 'widthPerBay'))
  const d = Math.max(num(p, 'minDepth'), num(p, 'baseDepth') + Math.min(vNom, num(p, 'depthVoltageCap')) / num(p, 'kvPerDepthM'))
  const steel = str(p, 'steelColor')
  return {
    parts: [
      { pos: [0, 0, 0.15], size: [w, d, 0.3], color: str(p, 'padColor') },     // gravel pad
      { pos: [0, d / 4, 5], size: [w - 4, 0.4, 0.4], color: steel },           // busbars
      { pos: [0, -d / 4, 5], size: [w - 4, 0.4, 0.4], color: steel },
      { pos: [-w / 2 + 2, 0, 2.5], size: [0.4, d - 4, 5], color: steel },      // end gantries
      { pos: [w / 2 - 2, 0, 2.5], size: [0.4, d - 4, 5], color: steel },
    ],
    footprint: [w, d], areaM2: w * d, count: 1, each: 1, anchors: {},
  }
}

function manifold(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const bays = input.bays ?? 0
  const w = Math.max(num(p, 'minWidth'), num(p, 'baseWidth') + bays * num(p, 'widthPerBay')), d = num(p, 'depth')
  const pipe = str(p, 'pipeColor'), dia = num(p, 'pipeDiameter'), z = num(p, 'rackHeight')
  const parts: Part[] = [{ pos: [0, 0, 0.1], size: [w, d, 0.2], color: str(p, 'padColor') }]
  for (const y of [-d / 4, 0, d / 4]) parts.push({ pos: [0, y, z], size: [w - 2, dia, dia], shape: 'cylinder', axis: 'east', color: pipe })
  for (const x of [-w / 2 + 1, w / 2 - 1]) parts.push({ pos: [x, 0, z / 2], size: [0.4, d - 2, z], color: str(p, 'steelColor') })
  return { parts, footprint: [w, d], areaM2: w * d, count: 1, each: 1, anchors: {} }
}

function transformer(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  // Tank grows with the cube root of rating: 10 MVA ≈ 5 m, 400 MVA ≈ 12 m.
  const s = Math.max(1, Math.cbrt(Math.max(input.amount, 1)))
  const tank: [number, number, number] = [2.3 * s, 1.4 * s, 1.6 * s]
  const rad = str(p, 'radiatorColor'), bushing = str(p, 'bushingColor')
  const clear = num(p, 'clearance')
  return {
    parts: [
      { pos: [0, 0, tank[2] / 2], size: tank },
      { pos: [-tank[0] / 2 - 0.6, 0, tank[2] / 2], size: [1.2, tank[1] * 0.8, tank[2] * 0.9], color: rad },
      { pos: [tank[0] / 2 + 0.6, 0, tank[2] / 2], size: [1.2, tank[1] * 0.8, tank[2] * 0.9], color: rad },
      { pos: [-tank[0] / 4, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: bushing },
      { pos: [0, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: bushing },
      { pos: [tank[0] / 4, 0, tank[2] + 1], size: [0.4, 0.4, 2], color: bushing },
    ],
    footprint: [tank[0] + clear, tank[1] + clear], areaM2: (tank[0] + clear) * (tank[1] + clear), count: 1, each: 1,
    anchors: { flow: { from: [0, -tank[1] / 2 - 1, tank[2] + 2.5], to: [0, tank[1] / 2 + 1, tank[2] + 2.5] } },
  }
}

function bay(p: Record<string, ParamValue>): TemplateOutput {
  const w = num(p, 'width'), d = num(p, 'depth'), h = num(p, 'gantryHeight')
  return {
    parts: [
      { pos: [0, 0, 0.15], size: [w, d, 0.3], color: str(p, 'slabColor') },
      { pos: [-w / 3, 0, h / 2], size: [0.3, 0.3, h] }, { pos: [w / 3, 0, h / 2], size: [0.3, 0.3, h] },
      { pos: [0, 0, h], size: [w * 2 / 3 + 0.6, 0.3, 0.3] },
    ],
    footprint: [w, d], areaM2: w * d, count: 1, each: 1,
    anchors: { flow: { from: [0, -d / 2, h + 0.8], to: [0, d / 2, h + 0.8] } },
  }
}

function unitGrid(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const unit: [number, number, number] = [num(p, 'unitW'), num(p, 'unitD'), num(p, 'unitH')]
  const perRow = num(p, 'perRow'), gap = num(p, 'gap')
  const n = count(input.amount, num(p, 'per'))
  const { drawn, each } = capped(n)
  const shape = (opt(p, 'shape') ?? 'box') as Shape
  const g = gridParts(drawn, unit, perRow, gap, { shape, axis: shape === 'cylinder' ? 'up' : undefined, heroable: p.heroable === true })
  const rows = Math.ceil(drawn / perRow)
  const extra = opt(p, 'extra') ?? 'none'
  const extraColor = opt(p, 'extraColor')
  const anchors: Anchors = {}
  if (extra === 'pcs') {
    // A PCS / inverter skid per row, on its west side.
    for (let r = 0; r < rows; r++) g.parts.push({ pos: [-g.footprint[0] / 2 - 3, -g.footprint[1] / 2 + unit[1] / 2 + r * (unit[1] + gap), 1.2], size: [2.4, 2.4, 2.4], color: extraColor })
    anchors.fill = gaugeAbove(g.footprint[0], unit[2])
    g.footprint[0] += 6
  } else if (extra === 'stack') {
    // One exhaust stack per row, on its east side.
    for (let r = 0; r < rows; r++) g.parts.push({ pos: [g.footprint[0] / 2 + 2, -g.footprint[1] / 2 + unit[1] / 2 + r * (unit[1] + gap), 10], size: [1.5, 1.5, 20], shape: 'cylinder', axis: 'up', color: extraColor })
    g.footprint[0] += 4
  } else if (extra === 'bop') {
    // Compressor / balance-of-plant building alongside, to the north.
    g.parts.push({ pos: [0, g.footprint[1] / 2 + 8, 3], size: [Math.max(15, g.footprint[0]), 10, 6], color: extraColor })
    g.footprint[1] += 16
    g.footprint[0] = Math.max(g.footprint[0], 15)
  } else if (extra === 'coolers') {
    // A row of dry coolers to the north.
    g.parts.push({ pos: [0, g.footprint[1] / 2 + 4, 1.5], size: [Math.max(6, g.footprint[0]), 4, 3], color: extraColor })
    g.footprint[1] += 6
  }
  if (p.anchor === 'fill' && !anchors.fill) anchors.fill = gaugeAbove(g.footprint[0], unit[2])
  if (p.anchor === 'emissive') anchors.emissive = true
  return { parts: g.parts, footprint: g.footprint, areaM2: g.footprint[0] * g.footprint[1], count: n, each, anchors }
}

function tankArray(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const dia = num(p, 'diameter'), len = num(p, 'length')
  const axis = str(p, 'axis') as Axis
  const unit: [number, number, number] = axis === 'up' ? [dia, dia, len] : axis === 'north' ? [dia, len, dia] : [len, dia, dia]
  const n = count(input.amount, num(p, 'per'))
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, unit, num(p, 'perRow'), num(p, 'gap'), { shape: 'cylinder', axis, heroable: p.heroable === true })
  return {
    parts: g.parts, footprint: g.footprint, areaM2: g.footprint[0] * g.footprint[1], count: n, each,
    anchors: { fill: gaugeAbove(g.footprint[0], unit[2]) },
  }
}

function turbineArray(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const n = count(input.amount, num(p, 'per'))
  const { drawn, each } = capped(n)
  const hub = num(p, 'hubHeight'), rotor = num(p, 'rotorDiameter')
  const spacing = rotor * num(p, 'spacingD')
  const perRow = Math.min(drawn, num(p, 'perRow'))
  const cols = perRow, rows = Math.ceil(drawn / perRow)
  const w = (cols - 1) * spacing, d = (rows - 1) * spacing
  const tower = str(p, 'towerColor'), blade = str(p, 'bladeColor'), td = num(p, 'towerDiameter')
  const parts: Part[] = []
  const rotors: NonNullable<Anchors['rotors']> = []
  for (let i = 0; i < drawn; i++) {
    const c = i % perRow, r = Math.floor(i / perRow)
    const x = -w / 2 + c * spacing, y = -d / 2 + r * spacing
    parts.push({ pos: [x, y, hub / 2], size: [td, td, hub], shape: 'cylinder', axis: 'up', color: tower, heroable: true })   // tower
    parts.push({ pos: [x, y - 3, hub], size: [4, 10, 4], color: tower, heroable: true })                                      // nacelle
    const hubPos: [number, number, number] = [x, y - 8, hub]
    rotors.push({ turbine: i, hub: hubPos })
    for (let b = 0; b < 3; b++) {                                                                                             // blades, rotor facing south
      const a = b * (2 * Math.PI / 3)
      // Radial: the blade's east axis turns to (cos a, sin a) in the (east, up) plane, i.e. rotN = −a.
      parts.push({ pos: [x + Math.cos(a) * rotor / 4, y - 8, hub + Math.sin(a) * rotor / 4], size: [rotor / 2, 0.5, 3], rotN: -a, color: blade, anchor: 'rotor', turbine: i, heroable: true })
    }
  }
  const footprint: [number, number] = [w + rotor, d + rotor]
  return { parts, footprint, areaM2: footprint[0] * footprint[1] * each, count: n, each, anchors: { rotors } }
}

function pvField(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const mw = Math.max(input.amount, num(p, 'minMw'))
  const areaM2 = mw * num(p, 'haPerMwp') * 10_000
  const rowLength = num(p, 'rowLength'), pitch = num(p, 'rowPitch'), gap = num(p, 'tableGap')
  const width = Math.max(rowLength, Math.sqrt(areaM2))
  const tablesPerRow = Math.max(1, Math.round(width / (rowLength + gap)))
  const rows = Math.max(1, Math.round(areaM2 / (tablesPerRow * rowLength * pitch)))
  const n = rows * tablesPerRow
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, [rowLength, num(p, 'tableDepth'), 0.1], tablesPerRow, gap, { heroable: true })
  // Tilt every table toward the equator (south in the northern hemisphere;
  // the sign is cosmetic for a planning view). Lift so the low edge clears ground.
  for (const part of g.parts) { part.rotX = num(p, 'tiltDeg') * Math.PI / 180; part.pos[2] = num(p, 'lift') }
  g.footprint[1] = Math.max(g.footprint[1], rows * pitch)
  return { parts: g.parts, footprint: g.footprint, areaM2, count: n, each, anchors: { emissive: true } }
}

function pvRoof(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const mw = Math.max(input.amount, num(p, 'minMw'))
  const panelM2 = mw * num(p, 'm2PerMwp')
  const rowLength = num(p, 'rowLength'), depth = num(p, 'tableDepth'), gap = num(p, 'tableGap')
  const n = Math.max(1, Math.round(panelM2 / (rowLength * depth)))
  const tilt = num(p, 'tiltDeg') * Math.PI / 180
  if (input.roof) {
    // As many tables as the free roof holds; beyond that each drawn table stands for several.
    const perRow = Math.floor((input.roof.w + gap) / (rowLength + gap))
    const rows = Math.floor((input.roof.d + gap) / (depth + gap))
    const fit = perRow * rows
    if (fit >= 1) {
      const drawn = Math.min(n, fit, MAX_UNITS)
      const each = Math.ceil(n / drawn)
      const g = gridParts(drawn, [rowLength, depth, 0.1], perRow, gap, { heroable: true })
      for (const part of g.parts) { part.rotX = tilt; part.pos[1] += input.roof.cy; part.pos[2] = 0.4 }
      return { parts: g.parts, footprint: [input.roof.w, input.roof.d], areaM2: 0, count: n, each, anchors: { emissive: true }, fitsRoof: true }
    }
  }
  const side = Math.sqrt(panelM2 * 1.6)
  const perRow = Math.max(1, Math.round(side / (rowLength + gap)))
  const { drawn, each } = capped(n)
  const g = gridParts(drawn, [rowLength, depth, 0.1], perRow, gap, { heroable: true })
  const lift = num(p, 'canopyHeight')
  for (const part of g.parts) { part.rotX = tilt; part.pos[2] = lift }
  // A canopy frame: four posts under the corners.
  const [w, d] = g.footprint
  for (const [x, y] of [[-w / 2, -d / 2], [w / 2, -d / 2], [-w / 2, d / 2], [w / 2, d / 2]]) {
    g.parts.push({ pos: [x, y, lift / 2], size: [0.3, 0.3, lift], color: str(p, 'postColor') })
  }
  return { parts: g.parts, footprint: g.footprint, areaM2: 0, count: n, each, anchors: { emissive: true }, fitsRoof: input.roof ? false : undefined }
}

function hall(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const areaM2 = Math.max(num(p, 'minM2'), Math.abs(input.amount) * num(p, 'm2PerMw'))
  const w = Math.sqrt(areaM2 * num(p, 'aspect')), d = areaM2 / w, h = num(p, 'height')
  return {
    parts: [
      { pos: [0, 0, h / 2], size: [w, d, h], heroable: true },
      // Rooftop plant strip so it reads as a building, not a slab.
      { pos: [0, d / 4, h + 1.5], size: [w * 0.6, d * 0.2, 3], color: str(p, 'roofColor') },
    ],
    footprint: [w, d], areaM2, count: 1, each: 1, anchors: { emissive: true },
  }
}

function reservoir(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const basinM2 = Math.max(num(p, 'minM2'), Math.max(input.amount, 0) * num(p, 'm2PerMwh'))
  const side = Math.sqrt(basinM2), wall = num(p, 'wallHeight')
  const ph: [number, number, number] = [num(p, 'powerhouseW'), num(p, 'powerhouseD'), num(p, 'powerhouseH')]
  const embank = str(p, 'embankmentColor')
  const parts: Part[] = [
    { pos: [0, 0, 0.2], size: [side, side, 0.4], color: str(p, 'waterColor') },          // basin floor / water
    { pos: [0, side / 2, wall / 2], size: [side + 4, 2, wall], color: embank },           // embankments
    { pos: [0, -side / 2, wall / 2], size: [side + 4, 2, wall], color: embank },
    { pos: [-side / 2, 0, wall / 2], size: [2, side, wall], color: embank },
    { pos: [side / 2, 0, wall / 2], size: [2, side, wall], color: embank },
    { pos: [side / 2 + 4 + ph[0] / 2, 0, ph[2] / 2], size: ph, color: str(p, 'powerhouseColor') },
  ]
  const footprint: [number, number] = [side + 8 + ph[0], side + 4]
  // Land: the basin inside its embankments, plus the powerhouse.
  return {
    parts, footprint, areaM2: (side + 4) * (side + 4) + ph[0] * ph[1], count: 1, each: 1,
    anchors: { fill: { pos: [0, 0, wall / 2], size: [side - 2, side - 2, wall] } },
  }
}

function plant(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  const areaM2 = Math.max(num(p, 'minM2'), Math.max(input.amount, 0) * num(p, 'm2PerMw'))
  const w = Math.sqrt(areaM2 * 2), d = areaM2 / w, h = num(p, 'hallHeight')
  const parts: Part[] = [{ pos: [0, 0, h / 2], size: [w, d, h] }]
  const extra = opt(p, 'extra') ?? 'none'
  const extraColor = opt(p, 'extraColor')
  let fw = w, fd = d
  if (extra === 'hrsg') {
    // Heat-recovery steam generator beside the turbine hall.
    const hw = d * 0.6
    parts.push({ pos: [w / 2 + hw / 2 + 2, 0, num(p, 'extraHeight') / 2], size: [hw, d * 0.8, num(p, 'extraHeight')], color: extraColor })
    fw += hw + 2
  } else if (extra === 'heatExchanger') {
    parts.push({ pos: [w / 2 + 4, 0, 2], size: [6, d * 0.5, 4], color: extraColor })
    fw += 8
  } else if (extra === 'receivers') {
    // Pressure receivers north of the hall.
    const nRec = count(input.amount, num(p, 'mwPerReceiver'))
    const { drawn } = capped(nRec)
    const g = gridParts(drawn, [4, 4, 12], 8, 2, { shape: 'cylinder', axis: 'up', color: extraColor })
    for (const part of g.parts) { part.pos[1] += d / 2 + 4 + g.footprint[1] / 2; parts.push(part) }
    fd += g.footprint[1] + 4
    fw = Math.max(fw, g.footprint[0])
  }
  const sd = num(p, 'stackDiameter'), sh = num(p, 'stackHeight')
  if (sh > 0.01) parts.push({ pos: [-w / 2 + sd, -d / 2 - sd, sh / 2], size: [sd, sd, sh], shape: 'cylinder', axis: 'up', color: str(p, 'stackColor') })
  return { parts, footprint: [fw, fd + sd * 2], areaM2: fw * (fd + sd * 2), count: 1, each: 1, anchors: { emissive: true } }
}

function cube(p: Record<string, ParamValue>, input: TemplateInput): TemplateOutput {
  // A tank whose volume grows with the energy — anything the library has no type for.
  const side = Math.max(num(p, 'minSide'), Math.cbrt(Math.max(input.amount, 1)) * 2)
  return {
    parts: [{ pos: [0, 0, side / 2], size: [side, side, side] }],
    footprint: [side, side], areaM2: side * side, count: 1, each: 1,
    anchors: { fill: gaugeAbove(side, side) },
  }
}

const RUN: Record<TemplateId, (p: Record<string, ParamValue>, input: TemplateInput) => TemplateOutput> = {
  yard, manifold, transformer, bay: p => bay(p), unitGrid, tankArray, turbineArray, pvField, pvRoof, hall, reservoir, plant, cube,
}

/**
 * Run a template, then centre its parts inside the footprint: some
 * templates add an extra (a PCS row, a stack, a heat-recovery boiler) on one
 * side while the footprint grows on both, and a part sticking out of its
 * footprint cuts into its neighbour (WP1 review gate). Rotations are ignored
 * for the extent (an over-estimate for tilted or turned parts).
 */
export function runTemplate(geometry: Geometry, input: TemplateInput): TemplateOutput {
  const out = RUN[geometry.template](geometry.params, input)
  // Placed on a roof: its offset is the free part of the roof, not a centring error.
  if (!out.parts.length || out.fitsRoof) return out
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  for (const q of out.parts) {
    if (q.anchor === 'rotor') continue
    x0 = Math.min(x0, q.pos[0] - q.size[0] / 2); x1 = Math.max(x1, q.pos[0] + q.size[0] / 2)
    y0 = Math.min(y0, q.pos[1] - q.size[1] / 2); y1 = Math.max(y1, q.pos[1] + q.size[1] / 2)
  }
  const dx = -(x0 + x1) / 2, dy = -(y0 + y1) / 2
  if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9) return out
  const shift = (v: [number, number, number]): [number, number, number] => [v[0] + dx, v[1] + dy, v[2]]
  for (const q of out.parts) q.pos = shift(q.pos)
  const a = out.anchors
  if (a.fill) a.fill = { ...a.fill, pos: shift(a.fill.pos) }
  if (a.flow) a.flow = { from: shift(a.flow.from), to: shift(a.flow.to) }
  if (a.rotors) a.rotors = a.rotors.map(r => ({ ...r, hub: shift(r.hub) }))
  return out
}
