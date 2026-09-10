// busDivIcon builds its marker as an HTML STRING, so the bus name has to be
// attribute-escaped on the way in. A bus called `A"B` would otherwise close
// the attribute early and produce markup whose data-bus-name is 'A'.
import { describe, expect, it } from 'vitest'
import { busDivIcon } from './MapCanvas'

describe('busDivIcon', () => {
  it('emits the bus name as data-bus-name', () => {
    const html = busDivIcon('#ff0000', 'DE0 0').options.html as string
    expect(html).toContain('data-bus-name="DE0 0"')
  })

  it('escapes the four characters that would break out of the attribute', () => {
    const html = busDivIcon('#ff0000', `A"B&C<D>E`).options.html as string
    expect(html).toContain('data-bus-name="A&quot;B&amp;C&lt;D&gt;E"')
  })

  it('keeps the marker class the rest of MapCanvas styles against', () => {
    expect(busDivIcon('#ff0000', 'B1').options.className).toBe('pypsa-bus-marker')
  })

  it('parses back to an element whose attribute is the original name', () => {
    const wrapper = document.createElement('div')
    wrapper.innerHTML = busDivIcon('#ff0000', `A"B`).options.html as string
    expect(
      wrapper.firstElementChild?.getAttribute('data-bus-name'),
    ).toBe(`A"B`)
  })
})
