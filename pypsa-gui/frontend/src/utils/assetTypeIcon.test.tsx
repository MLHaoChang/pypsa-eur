// Visual-layers plan 3 S1: one icon per asset type, drawn from the palette's set.
import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ASSET_TYPE_ICONS, assetIcon, iconFor } from './assetTypeIcon'
import { ASSET_ICON_NAMES, ASSET_TYPES } from './assetTypes'
import { PALETTE_ICONS } from '../layout/paletteIcons'

describe('asset type icons', () => {
  it('every icon name has an icon that renders an svg', () => {
    for (const name of ASSET_ICON_NAMES) {
      expect(ASSET_TYPE_ICONS[name], name).toBeTruthy()
      expect(renderToStaticMarkup(<>{assetIcon(name)}</>), name).toMatch(/<svg/)
    }
  })
  it('every asset type resolves to an icon; an unknown id to null', () => {
    for (const t of ASSET_TYPES) expect(iconFor(t.id), t.id).toBeTruthy()
    expect(iconFor('teapot')).toBeNull()
  })
  it('a palette-named icon is the palette\'s own node, so the sidebar and the legends share the glyph', () => {
    for (const id of Object.keys(PALETTE_ICONS)) {
      expect(ASSET_TYPE_ICONS[id as keyof typeof ASSET_TYPE_ICONS], id).toBe(PALETTE_ICONS[id])
    }
  })
})
