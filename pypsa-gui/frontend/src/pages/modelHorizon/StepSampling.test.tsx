// Edge Investment Case WP1.0: on a sub-hourly axis the backend refuses
// representative-week sampling with `not_supported_for_freq`. The panel must
// say THAT, not tell the user to upload an hourly profile they already have.
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StepSampling } from './StepSampling'

const base = {
  sampleNWeeks: '1', onSampleNWeeksChange: () => {}, sampleSeed: '', onSampleSeedChange: () => {},
  onSampleWeeks: () => {}, sampleWeeksPending: false, sampledWeeks: [],
}

describe('StepSampling disabled reason', () => {
  it('explains a sub-hourly axis', () => {
    render(<StepSampling {...base} canSampleWeeks={false} sampleWeeksReason="not_supported_for_freq" />)
    expect(screen.getByTestId('sample-weeks-disabled').textContent).toMatch(/sub-hourly/)
  })
  it('keeps the upload hint otherwise', () => {
    render(<StepSampling {...base} canSampleWeeks={false} sampleWeeksReason={null} />)
    expect(screen.getByTestId('sample-weeks-disabled').textContent).toMatch(/upload a full-year hourly profile/)
  })
})
