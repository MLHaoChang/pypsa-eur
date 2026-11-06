// UIP-01: a refused Properties save shows ONE toast, top-centre (clear of Save).
import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import toast, { useToasterStore } from 'react-hot-toast'
import { saveFailedToast } from './toasts'

describe('saveFailedToast', () => {
  it('replaces the interceptor toast instead of stacking beside it', () => {
    const { result } = renderHook(() => useToasterStore())
    act(() => {
      toast.error('A study is running: wait for it', { id: 'A study is running: wait for it' }) // the interceptor's
      saveFailedToast('A study is running: wait for it')
    })
    const live = result.current.toasts
    expect(live.map(t => t.message)).toEqual(['Save failed: A study is running: wait for it'])
    expect(live[0].position).toBe('top-center')
  })
})
