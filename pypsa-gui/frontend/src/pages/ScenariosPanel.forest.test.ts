// `buildScenarioForest` reconstructs the scenario tree client-side from the
// flat project list's `parent_project` pointers, and carries hand-written
// defences for self-parents, dangling pointers and mutual cycles — none of
// which had a test. It is shared by ScenariosPanel and WorkspacePanel, so a
// regression here empties or duplicates rows in two places at once.
//
// The load-bearing property, asserted on every shape below: EVERY project in
// the input appears EXACTLY ONCE in the output. A project that vanishes from
// the tree is unreachable in the UI; one that appears twice gets two delete
// buttons pointing at the same directory.
import { describe, expect, it } from 'vitest'
import { buildScenarioForest, describeDescendants, type ScenarioNode } from './ScenariosPanel'
import type { ProjectInfo } from '../api/types'

const p = (name: string, parent: string | null = null): ProjectInfo => ({
  name,
  created_at: '2026-01-01T00:00:00',
  has_solver_config: true,
  bus_count: 3,
  snapshot_count: 24,
  objective: null,
  parent_project: parent,
})

/** Every node in the forest, depth-first — the order the panel renders. */
function flatten(nodes: ScenarioNode[]): ScenarioNode[] {
  return nodes.flatMap(n => [n, ...flatten(n.children)])
}

const names = (nodes: ScenarioNode[]) => flatten(nodes).map(n => n.project.name)

/** The invariant. Sorted, so it is about membership rather than layout. */
function expectCoversExactlyOnce(forest: ScenarioNode[], input: ProjectInfo[]) {
  expect([...names(forest)].sort()).toEqual([...input.map(x => x.name)].sort())
}

describe('buildScenarioForest', () => {
  it('returns nothing for no projects', () => {
    expect(buildScenarioForest([])).toEqual([])
  })

  it('treats parentless projects as roots, alphabetised', () => {
    const input = [p('zulu'), p('alpha'), p('mike')]
    const forest = buildScenarioForest(input)
    expect(forest.map(n => n.project.name)).toEqual(['alpha', 'mike', 'zulu'])
    expect(forest.every(n => n.depth === 0)).toBe(true)
    expectCoversExactlyOnce(forest, input)
  })

  it('nests children under their parent and stamps depth', () => {
    const input = [p('base'), p('mid', 'base'), p('leaf', 'mid')]
    const forest = buildScenarioForest(input)
    expect(forest).toHaveLength(1)
    expect(names(forest)).toEqual(['base', 'mid', 'leaf'])
    expect(flatten(forest).map(n => n.depth)).toEqual([0, 1, 2])
    expectCoversExactlyOnce(forest, input)
  })

  it('alphabetises siblings at every level, not just the roots', () => {
    const input = [
      p('base'), p('b_scen', 'base'), p('a_scen', 'base'),
      p('z_leaf', 'a_scen'), p('y_leaf', 'a_scen'),
    ]
    const forest = buildScenarioForest(input)
    expect(names(forest)).toEqual(['base', 'a_scen', 'y_leaf', 'z_leaf', 'b_scen'])
  })

  it('promotes a self-parent to a root, and not into its own children', () => {
    // A === A.parent. Two mechanisms cover this — the explicit self-parent
    // check, and the cycle pass behind it — so removing either one alone
    // leaves the behaviour intact. The assertion is therefore on the outcome
    // (one row, no self-child), which is what breaks if BOTH ever go.
    const input = [p('loner', 'loner')]
    const forest = buildScenarioForest(input)
    expect(forest.map(n => n.project.name)).toEqual(['loner'])
    expect(forest[0].children).toEqual([])
    expectCoversExactlyOnce(forest, input)
  })

  it('surfaces a project whose parent is no longer on disk', () => {
    // The parent was deleted in Finder, so the pointer names a project the
    // list no longer carries. The orphan must still be reachable.
    const input = [p('orphan', 'deleted_base'), p('normal')]
    const forest = buildScenarioForest(input)
    expect(forest.map(n => n.project.name)).toEqual(['normal', 'orphan'])
    expect(forest.find(n => n.project.name === 'orphan')!.depth).toBe(0)
    expectCoversExactlyOnce(forest, input)
  })

  it('renders both members of a mutual cycle exactly once', () => {
    // A.parent = B and B.parent = A: neither is a root, so the first pass
    // produces an EMPTY root list and every node is unreachable.
    const input = [p('A', 'B'), p('B', 'A')]
    const forest = buildScenarioForest(input)
    expectCoversExactlyOnce(forest, input)
    expect(forest).toHaveLength(1)
  })

  it('renders a three-way cycle exactly once', () => {
    const input = [p('A', 'C'), p('B', 'A'), p('C', 'B')]
    const forest = buildScenarioForest(input)
    expectCoversExactlyOnce(forest, input)
  })

  it('keeps a clean subtree hanging off a cycle reachable', () => {
    // `hanger` is not in the cycle but its only route to a root runs through
    // one. Detaching the cycle carelessly strands it.
    const input = [p('A', 'B'), p('B', 'A'), p('hanger', 'B'), p('clean')]
    const forest = buildScenarioForest(input)
    expectCoversExactlyOnce(forest, input)
  })

  it('keeps two independent cycles from swallowing each other', () => {
    const input = [p('A', 'B'), p('B', 'A'), p('X', 'Y'), p('Y', 'X')]
    const forest = buildScenarioForest(input)
    expectCoversExactlyOnce(forest, input)
    expect(forest).toHaveLength(2)
  })

  it('does not mutate the projects it was handed', () => {
    // Both panels build a forest from the SAME React Query cache entry; a
    // builder that wrote back onto the rows would corrupt the other's view.
    const input = [p('base'), p('child', 'base')]
    const snapshot = JSON.parse(JSON.stringify(input))
    buildScenarioForest(input)
    expect(input).toEqual(snapshot)
  })
})

describe('describeDescendants', () => {
  it('names the children from the structured 409 detail', () => {
    const msg = describeDescendants(
      { error_kind: 'descendants_exist', message: 'Pass ?cascade=true …', descendants: ['a', 'b'] },
      'base',
    )
    expect(msg).toBe("'base' has 2 child scenarios (a, b). Delete it and all of them?")
  })

  it('uses the singular for one child', () => {
    expect(describeDescendants({ descendants: ['solo'] }, 'base'))
      .toBe("'base' has 1 child scenario (solo). Delete it and all of them?")
  })

  it('caps a long list rather than filling the toast', () => {
    const kids = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    expect(describeDescendants({ descendants: kids }, 'base'))
      .toBe("'base' has 7 child scenarios (a, b, c, d, e, +2 more). Delete it and all of them?")
  })

  it('never renders the raw detail object', () => {
    // The bug this replaced: `${detail}` on the backend's dict produced the
    // literal string "[object Object]" inside the confirm prompt.
    for (const detail of [
      { error_kind: 'descendants_exist', message: 'x', descendants: ['a'] },
      { descendants: [] },
      'a plain string detail',
      null,
      undefined,
    ]) {
      expect(describeDescendants(detail, 'base')).not.toContain('[object Object]')
    }
  })

  it('still asks when the detail has no readable descendants', () => {
    // A 409 we could not parse is still a 409 — dropping the prompt would
    // leave the user with a delete button that silently does nothing.
    expect(describeDescendants('unexpected shape', 'base'))
      .toBe("'base' still has child scenarios. Delete it and all of them?")
  })

  it('never leaks the API instruction from the backend message', () => {
    const msg = describeDescendants(
      { message: "Cannot delete 'base' — … Pass ?cascade=true to delete recursively.", descendants: ['a'] },
      'base',
    )
    expect(msg).not.toContain('cascade=true')
  })
})
