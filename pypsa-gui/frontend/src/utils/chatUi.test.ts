import { describe, expect, it } from 'vitest'
import { extractCopyText, isNearBottom } from './chatUi'

describe('chatUi helpers (Track A)', () => {
  describe('A2 isNearBottom', () => {
    it('is true when within threshold of the bottom', () => {
      expect(
        isNearBottom({ scrollHeight: 1000, scrollTop: 930, clientHeight: 50 }, 80),
      ).toBe(true)
    })

    it('is false when scrolled up past the threshold', () => {
      expect(
        isNearBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 50 }, 80),
      ).toBe(false)
    })
  })

  describe('A3 extractCopyText', () => {
    it('flattens nested children into a string', () => {
      const tree = {
        props: {
          children: [
            'line1\n',
            { props: { children: 'line2' } },
          ],
        },
      }
      expect(extractCopyText(tree)).toBe('line1\nline2')
    })
  })
})
