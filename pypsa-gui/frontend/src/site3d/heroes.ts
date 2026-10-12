// Hero models (Phase 2 spec §5, E7, E8; plan Tasks 3.1–3.3). Pure, no three.
//
// Five CC0 models from Kenney's City Kit Industrial 2.0 stand in for the
// parametric units they look like: containers, turbines, tanks, PV tables,
// halls. The manifest records, per model, what the file holds (bounds in
// model space after its node transforms, a mirror removed) and how it is
// fitted onto a parametric unit: a base yaw onto (east, north, up) and a
// fit mode. `heroInstances` turns an object's heroable parts into one
// transform per instance; the three.js side (heroLoader.tsx) only multiplies
// matrices. Model space: X east, Y up, Z south (glTF/three), before the yaw.

import type { Part } from './templates'

export type FitMode =
  /** Scale each axis to the unit's box (containers, tanks). */
  | 'box'
  /** Uniform scale so the rotor hub sits at the tower's height and on the parametric hub; blades scaled to the rotor diameter (turbines). */
  | 'hub'
  /** Uniform scale to the unit's height (halls) or depth (tables), tiled along the rest with a mild stretch to fill. */
  | 'tile'

export interface HeroModel {
  id: 'container' | 'turbine' | 'pvTable' | 'tank' | 'hall'
  /** File under public/site3d/models/. */
  file: string
  /** Bounds of the whole model in model space, after its node transforms (a mirroring root scale removed). */
  bounds: { min: [number, number, number]; max: [number, number, number] }
  /** The root node's own scale as the file has it (a negative component = the mirror we remove). */
  rootScale: [number, number, number]
  /** Turn about up, degrees, applied before fitting, so the model's long axis matches the unit's. */
  yawDeg: number
  fit: FitMode
  /** 'tile' only: which unit dimension sets the uniform scale. */
  tileBy?: 'height' | 'depth'
  /** Turbines: the node that spins, its pivot (the node's translation, model space; the node has no rotation, so it spins about its local X) and the blade tip radius. */
  rotor?: { node: string; hub: [number, number, number]; axis: 'x'; radius: number }
  /** Draw into the shadow map (default true). Off for PV tables: hundreds of low tables, little to see. */
  castShadow?: boolean
}

// Measured from the processed files (glbJson.ts; heroes.test.ts pins them).
export const HERO_MODELS: Record<HeroModel['id'], HeroModel> = {
  container: {
    id: 'container', file: 'shipping-container-a.glb', rootScale: [0.27, 0.27, 0.27],
    bounds: { min: [-0.1863, 0, -0.4113], max: [0.1863, 0.3481, 0.4113] },
    yawDeg: 90, fit: 'box',
  },
  turbine: {
    id: 'turbine', file: 'windmill.glb', rootScale: [1, 1, 1],
    bounds: { min: [-0.3154, 0, -0.5667], max: [0.2893, 2.3139, 0.5602] },
    yawDeg: 90, fit: 'hub', rotor: { node: 'blades', hub: [-0.240356654, 1.67635334, -0.000808686], axis: 'x', radius: 0.6375 },
  },
  pvTable: {
    id: 'pvTable', file: 'solar-panel-landscape-group.glb', rootScale: [1, 1, 1],
    bounds: { min: [-0.7567, 0, -0.4475], max: [0.7567, 0.2624, 0.4475] },
    // The panels face the model's −Z: turned 180° they face south, like the parametric tables.
    yawDeg: 180, fit: 'tile', tileBy: 'depth', castShadow: false,
  },
  tank: {
    id: 'tank', file: 'detail-tank.glb', rootScale: [-1, 1, 1],
    bounds: { min: [-0.424, 0, -0.2576], max: [0.424, 0.4153, 0.2576] },
    yawDeg: 90, fit: 'box',
  },
  hall: {
    id: 'hall', file: 'building-s.glb', rootScale: [1, 1, 1],
    bounds: { min: [-1.07, 0, -0.4581], max: [1.05, 0.8368, 0.4581] },
    yawDeg: 0, fit: 'tile', tileBy: 'height',
  },
}

export const HERO_IDS = Object.keys(HERO_MODELS) as HeroModel['id'][]

/**
 * Instances per object, at most (spec E8: the packer's cap bounds the units;
 * this bounds the tiles, which the cap does not — a large PV field would
 * otherwise draw ~900 tables of ~1k triangles, a 500 MW hall ~1000 tiles).
 * Past it, each tile stretches along the row.
 */
export const MAX_HERO_INSTANCES = 240

/** The URL of a model file, relative to the app's base (never absolute, never a CDN). */
export const heroUrl = (m: HeroModel, base = '/'): string => `${base.replace(/\/?$/, '/')}site3d/models/${m.file}`

/** One instance, in the object's frame (metres east, north, up; yaw degrees clockwise from north like a heading). */
export interface HeroInstance {
  /** Where the model's origin (bottom centre of its bounds, after centring) goes. */
  pos: [number, number, number]
  /** Total turn about up, radians, counter-clockwise seen from above (three's Y rotation), including the model's base yaw. */
  yaw: number
  /** Scale along the model's own X, Y, Z. */
  scale: [number, number, number]
  /** Model-space offset that centres the model's bounds on its origin in X and Z (applied before scaling). */
  centre: [number, number, number]
  /** Turbines: the hub, in the object's frame (east, north, up) — the parametric rotor's hub. */
  hub?: [number, number, number]
  /** Turbines: the blades' scale about the hub (model units), on top of `scale`, so the rotor has the library's diameter. */
  rotorScale?: number
}

const size = (m: HeroModel): [number, number, number] => [m.bounds.max[0] - m.bounds.min[0], m.bounds.max[1] - m.bounds.min[1], m.bounds.max[2] - m.bounds.min[2]]
const centreOf = (m: HeroModel): [number, number, number] => [-(m.bounds.min[0] + m.bounds.max[0]) / 2, -m.bounds.min[1], -(m.bounds.min[2] + m.bounds.max[2]) / 2]

/**
 * A model yawed by ±90° lies with its X along north and its Z along east
 * (three's Y rotation by +90° takes X to −Z, i.e. north, and Z to +X, east);
 * yawed by 0° or 180°, X is east–west and Z is south–north.
 */
function axesAfterYaw(m: HeroModel): { eastAxis: 0 | 2; northAxis: 0 | 2 } {
  return Math.abs(m.yawDeg) % 180 === 90 ? { eastAxis: 2, northAxis: 0 } : { eastAxis: 0, northAxis: 2 }
}

const isUnit = (q: Part) => q.heroable === true && q.anchor !== 'rotor'

/** The parts a hero stands in for, and the per-unit transforms. */
export function heroInstances(parts: Part[], m: HeroModel): HeroInstance[] {
  const s = size(m), c = centreOf(m), yaw = (m.yawDeg * Math.PI) / 180
  const { eastAxis, northAxis } = axesAfterYaw(m)
  const out: HeroInstance[] = []
  if (m.fit === 'box') {
    for (const p of parts.filter(isUnit)) {
      // The unit's box in (east, north, up); a cylinder lying north is [d, L, d].
      const scale: [number, number, number] = [1, p.size[2] / s[1], 1]
      scale[eastAxis] = p.size[0] / s[eastAxis]
      scale[northAxis] = p.size[1] / s[northAxis]
      out.push({ pos: [p.pos[0], p.pos[1], p.pos[2] - p.size[2] / 2], yaw, scale, centre: c })
    }
  } else if (m.fit === 'hub') {
    const r = m.rotor!
    for (const p of parts.filter(q => isUnit(q) && q.shape === 'cylinder' && q.axis === 'up')) {
      const k = p.size[2] / (r.hub[1] + c[1])          // tower height = hub height
      // The hub's model-space offset from the model origin, turned by the yaw
      // (three's Y rotation: x' = x cos + z sin, z' = −x sin + z cos; scene z = −north).
      const hx = ((r.hub[0] + c[0]) * Math.cos(yaw) + (r.hub[2] + c[2]) * Math.sin(yaw)) * k
      const hz = (-(r.hub[0] + c[0]) * Math.sin(yaw) + (r.hub[2] + c[2]) * Math.cos(yaw)) * k
      // The model stands so its hub is on the parametric rotor's hub (the
      // centre of that turbine's blades), where WP6 anchors the spin.
      const blades = parts.filter(q => q.anchor === 'rotor' && q.turbine === p.turbine)
      const hub: [number, number, number] = blades.length
        ? [blades.reduce((a, q) => a + q.pos[0], 0) / blades.length, blades.reduce((a, q) => a + q.pos[1], 0) / blades.length, p.size[2]]
        : [p.pos[0] + hx, p.pos[1] - hz, p.size[2]]
      // Blade length = the rotor radius (a parametric blade is the radius long).
      const radius = blades.length ? Math.max(...blades.map(q => q.size[0])) : r.radius * k
      out.push({ pos: [hub[0] - hx, hub[1] + hz, 0], yaw, scale: [k, k, k], centre: c, hub, rotorScale: radius / (r.radius * k) })
    }
  } else {
    const units = parts.filter(isUnit)
    const grid = units.map(p => {
      const ref = m.tileBy === 'height' ? p.size[2] / s[1] : p.size[1] / s[northAxis]
      return { p, ref, nE: Math.max(1, Math.round(p.size[0] / (s[eastAxis] * ref))), nN: m.tileBy === 'height' ? Math.max(1, Math.round(p.size[1] / (s[northAxis] * ref))) : 1 }
    })
    // Over the cap: fewer, longer tiles, the same cut for every unit — along
    // the row for a single row (a table), along both axes for a grid (a hall).
    const total = grid.reduce((a, g) => a + g.nE * g.nN, 0)
    if (total > MAX_HERO_INSTANCES) {
      const r = MAX_HERO_INSTANCES / total
      for (const g of grid) {
        const f = g.nN === 1 ? r : Math.sqrt(r)
        g.nE = Math.max(1, Math.floor(g.nE * f))
        if (g.nN > 1) g.nN = Math.max(1, Math.floor(g.nN * f))
      }
    }
    while (grid.reduce((a, g) => a + g.nE * g.nN, 0) > MAX_HERO_INSTANCES) {
      const g = grid.reduce((a, b) => (b.nE * b.nN > a.nE * a.nN ? b : a))
      if (g.nE >= g.nN && g.nE > 1) g.nE--
      else if (g.nN > 1) g.nN--
      else break                                        // one tile per unit: the packer's cap bounds the rest
    }
    for (const { p, ref, nE, nN } of grid) {
      const scale: [number, number, number] = [ref, ref, ref]
      scale[eastAxis] = p.size[0] / (nE * s[eastAxis])
      scale[northAxis] = p.size[1] / (nN * s[northAxis])
      for (let i = 0; i < nE; i++) {
        for (let j = 0; j < nN; j++) {
          const e = p.pos[0] - p.size[0] / 2 + (i + 0.5) * (p.size[0] / nE)
          const n = p.pos[1] - p.size[1] / 2 + (j + 0.5) * (p.size[1] / nN)
          out.push({ pos: [e, n, p.base ?? 0], yaw, scale: [...scale] as [number, number, number], centre: c })
        }
      }
    }
  }
  return out
}
