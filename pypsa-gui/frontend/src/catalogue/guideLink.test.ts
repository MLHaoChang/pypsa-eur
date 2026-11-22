import { describe, expect, it } from 'vitest'
import { GUIDE_LINK_RE, guideLinkId, guideUrlTransform } from './guideLink'

describe('guide links', () => {
  it.each(['guide:generator.capacity', 'guide:asset_type.pvRoof', 'guide:function.load_flow'])('accepts %s', href => {
    expect(guideLinkId(href)).toBe(href.slice('guide:'.length))
    expect(GUIDE_LINK_RE.test(href)).toBe(true)
  })

  it.each(['guide:../x', 'guide:A', 'guide:A.b', 'guide:generator', ' guide:generator.capacity', 'GUIDE:generator.capacity',
    'guide:generator.capacity?x=1', 'guide:generator.capacity/..', 'guide:generator..capacity', 'guide://generator.capacity', undefined, 3])(
    'refuses %s', href => {
      expect(guideLinkId(href)).toBeNull()
    })
})

describe('guide URL filter', () => {
  it.each(['https://pypsa.readthedocs.io/', 'http://example.com/a'])('keeps the web link %s', url => {
    expect(guideUrlTransform(url, 'href')).toBe(url)
  })

  it('keeps a guide link as a link target only', () => {
    expect(guideUrlTransform('guide:generator.capacity', 'href')).toBe('guide:generator.capacity')
    expect(guideUrlTransform('guide:generator.capacity', 'src')).toBe('')
  })

  it.each(['javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' javascript:alert(1)', '\tjavascript:alert(1)', 'vbscript:msgbox(1)',
    'data:text/html,<script>alert(1)</script>', '//evil.example/x', '/api/projects/x', 'evil.example', 'guide:../x', 'guide:A',
    'mailto:a@example.com', 'file:///etc/passwd', ''])('drops %j', url => {
    expect(guideUrlTransform(url, 'href')).toBe('')
  })
})
