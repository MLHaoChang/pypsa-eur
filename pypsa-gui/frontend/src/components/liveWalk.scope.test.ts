import { expect, it } from 'vitest'
import { keyResultsWalkRequested, openProjectRequested } from './liveWalk'

it('leaves a named project request for exact harness resolution', () => {
  expect(openProjectRequested('Open project Sensitivity X')).toBe(false)
})

it('leaves a named sensitivity walkthrough for scope resolution', () => {
  expect(keyResultsWalkRequested('Walk me through the key results in each tab for sensitivity X')).toBe(false)
})

it.each([
  'Open project Sensitivity X',
  'Open project "list"',
  'Open the project with id 8c395682-7994-42cb-bb21-d4e01c79aac',
  'Do not open the project',
  'Please do not open the projects',
  'Open the projects and delete Target',
  'Open project Target, then run a simulation',
  'Walk me through the key results in each tab for project Target',
  'Walk me through the key results in each tab for sensitivity X',
  'Walk me through the key results in each tab for run old-run',
  'Walk me through the key results in each tab between hours 2 and 3',
  'Show results tabs for Generator G1',
  'Do not show results tabs',
  'Open results tabs and change the storage costs',
  'Walk me through the key results in each tab\nthen start the solve',
])('keeps the full qualified or compound request with the harness: %s', (text) => {
  expect(openProjectRequested(text)).toBe(false)
  expect(keyResultsWalkRequested(text)).toBe(false)
})

it.each(['Open the project', 'open projects', 'Please open the project list.', 'Open the project picker!'])
('keeps the explicit generic picker command available: %s', text => {
  expect(openProjectRequested(text)).toBe(true)
  expect(keyResultsWalkRequested(text)).toBe(false)
})

it.each(['Walk me through the key results in each tab', 'Show results tabs',
  'Please open the current results tabs.', '  WALK me through the key results in each tab!  '])
('keeps an explicit generic current-scope walkthrough available: %s', text => {
  expect(keyResultsWalkRequested(text)).toBe(true)
  expect(openProjectRequested(text)).toBe(false)
})
