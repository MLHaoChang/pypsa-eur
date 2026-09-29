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
  /** Uniform scale so the rotor hub sits at the tower's height (turbines). */
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
  /** Turbines: the node that spins and its pivot (the node's translation, model space). */
  rotor?: { node: string; hub: [number, number, number]; axis: 'x' }
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
    yawDeg: 90, fit: 'hub', rotor: { node: 'blades', hub: [-0.24, 1.676, -0.001], axis: 'x' },
  },
  pvTable: {
    id: 'pvTable', file: 'solar-panel-landscape-group.glb', rootScale: [1, 1, 1],
    bounds: { min: [-0.7567, 0, -0.4475], max: [0.7567, 0.2624, 0.4475] },
    yawDeg: 0, fit: 'tile', tileBy: 'depth',
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
  /** Turbines: the hub, in the object's frame (east, north, up). */
  hub?: [number, number, number]
}

const size = (m: HeroModel): [number, number, number] => [m.bounds.max[0] - m.bounds.min[0], m.bounds.max[1] - m.bounds.min[1], m.bounds.max[2] - m.bounds.min[2]]
const centreOf = (m: HeroModel): [number, number, number] => [-(m.bounds.min[0] + m.bounds.max[0]) / 2, -m.bounds.min[1], -(m.bounds.min[2] + m.bounds.max[2]) / 2]

/**
 * A model yawed by 90° lies with its X along north and its Z along east
 * (three's Y rotation by +90° takes X to −Z, i.e. north, and Z to +X, east);
 * unyawed, X is east and Z is south–north.
 */
function axesAfterYaw(m: HeroModel): { eastAxis: 0 | 2; northAxis: 0 | 2 } {
  return m.yawDeg === 90 ? { eastAxis: 2, northAxis: 0 } : { eastAxis: 0, northAxis: 2 }
}

/** The parts a hero stands in for, and the per-unit transforms. */
export function heroInstances(parts: Part[], m: HeroModel): HeroInstance[] {
  const s = size(m), c = centreOf(m), yaw = (m.yawDeg * Math.PI) / 180
  const { eastAxis, northAxis } = axesAfterYaw(m)
  const out: HeroInstance[] = []
  if (m.fit === 'box') {
    for (const p of parts.filter(q => q.heroable && q.anchor !== 'rotor')) {
      // The unit's box in (east, north, up); a cylinder lying north is [d, L, d].
      const scale: [number, number, number] = [1, p.size[2] / s[1], 1]
      scale[eastAxis] = p.size[0] / s[eastAxis]
      scale[northAxis] = p.size[1] / s[northAxis]
      out.push({ pos: [p.pos[0], p.pos[1], p.pos[2] - p.size[2] / 2], yaw, scale, centre: c })
    }
  } else if (m.fit === 'hub') {
    const hub = m.rotor!.hub
    for (const p of parts.filter(q => q.heroable && q.shape === 'cylinder' && q.axis === 'up')) {
      const k = p.size[2] / hub[1]                    // tower height = hub height
      // The hub's model-space offset from the tower axis, turned by the yaw
      // (three's Y rotation: x' = x cos + z sin, z' = −x sin + z cos; scene z = −north).
      const hx = (hub[0] + c[0]) * Math.cos(yaw) + (hub[2] + c[2]) * Math.sin(yaw)
      const hz = -(hub[0] + c[0]) * Math.sin(yaw) + (hub[2] + c[2]) * Math.cos(yaw)
      out.push({
        pos: [p.pos[0], p.pos[1], 0], yaw, scale: [k, k, k], centre: c,
        hub: [p.pos[0] + hx * k, p.pos[1] - hz * k, (hub[1] + c[1]) * k],
      })
    }
  } else {
    for (const p of parts.filter(q => q.heroable && q.anchor !== 'rotor')) {
      const ref = m.tileBy === 'height' ? p.size[2] / s[1] : p.size[1] / s[northAxis]
      const nE = Math.max(1, Math.round(p.size[0] / (s[eastAxis] * ref)))
      const nN = m.tileBy === 'height' ? Math.max(1, Math.round(p.size[1] / (s[northAxis] * ref))) : 1
      const scale: [number, number, number] = [ref, ref, ref]
      scale[eastAxis] = p.size[0] / (nE * s[eastAxis])
      scale[northAxis] = p.size[1] / (nN * s[northAxis])
      for (let i = 0; i < nE; i++) {
        for (let j = 0; j < nN; j++) {
          const e = p.pos[0] - p.size[0] / 2 + (i + 0.5) * (p.size[0] / nE)
          const n = p.pos[1] - p.size[1] / 2 + (j + 0.5) * (p.size[1] / nN)
          out.push({ pos: [e, n, 0], yaw, scale: [...scale] as [number, number, number], centre: c })
        }
      }
    }
  }
  return out
}

/**
 * The world-space box an instance covers (east, north, up), from the model's
 * bounds — what the tests compare with the parametric unit it replaces.
 */
export function instanceBox(inst: HeroInstance, m: HeroModel): { min: [number, number, number]; max: [number, number, number] } {
  const s = size(m)
  const { eastAxis, northAxis } = axesAfterYaw(m)
  const ext: [number, number, number] = [s[eastAxis] * inst.scale[eastAxis], s[northAxis] * inst.scale[northAxis], s[1] * inst.scale[1]]
  return {
    min: [inst.pos[0] - ext[0] / 2, inst.pos[1] - ext[1] / 2, inst.pos[2]],
    max: [inst.pos[0] + ext[0] / 2, inst.pos[1] + ext[1] / 2, inst.pos[2] + ext[2]],
  }
}
