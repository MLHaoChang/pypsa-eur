/**
 * A number input that keeps the typed text (IC P4 WP4.7 review B1).
 *
 * React resets a controlled `<input type="number">` whenever its string value
 * differs from the element's — so a string value turns "0.0" into "0" while
 * the user types 0.07, which then reads 7. This input holds the text locally,
 * reports the number (empty → null, never 0), and re-syncs from `value` only
 * when the value changes from outside (a reload), not while typing.
 */
import { useEffect, useState, type InputHTMLAttributes } from 'react'
import { numOrNull, numText } from './financeModel'

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & {
  value: unknown
  onChange: (v: number | null) => void
}

const parse = (text: string): number | null => {
  const n = numOrNull(text)
  return n !== null && Number.isNaN(n) ? null : n
}

export function NumInput({ value, onChange, ...rest }: Props) {
  const [text, setText] = useState(() => numText(value))
  useEffect(() => {
    const v = typeof value === 'number' ? value : null
    setText(t => (parse(t) === v ? t : numText(value)))
  }, [value])
  return (
    <input type="number" {...rest} value={text}
           onChange={e => { setText(e.target.value); onChange(parse(e.target.value)) }} />
  )
}
