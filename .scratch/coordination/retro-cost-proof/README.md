<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RETRO-COST-1 merge-order proof (R5)

`proof_edit.py <worktree> A|B|C` applies one proof branch's edits:

- **A** adds a GUI route without a tool (a `needs_tool` fragment and a `KNOWN_GAPS` row), a tool after `get_eh_readiness` and a kind fragment.
- **B** adds a tool serving `GET /api/campus-electrical/{name}/island/report`, a declared needs-tool and known-gap route that then shows as stale, and a kind fragment.
- **C** is like B, but appends after the same last tool as B.

Reproduce from the repository root at this branch's head:

```bash
S=$(mktemp -d)
for v in A B C; do
  b=proof-$(echo $v | tr A-Z a-z)
  git worktree add -q -B $b $S/wt-$v HEAD
  python3 .scratch/coordination/retro-cost-proof/proof_edit.py $S/wt-$v $v
  git -C $S/wt-$v add -A
  git -C $S/wt-$v -c user.name=proof -c user.email=proof@localhost commit -q -m "proof $v"
done
git merge-tree --write-tree --name-only proof-a proof-b   # clean
git merge-tree --write-tree --name-only proof-b proof-a   # clean, same tree
git merge-tree --write-tree --name-only proof-b proof-c   # conflicts: catalogue.py, chat_tools.py
```

On the A+B tree, all three `--check`s pass, with one `stale (sweep prunes)` line. The manifest, json_fragments, disposition, parity, inventory, argshape, handler-dependency and write-surface tests also pass. `python -m smoke.prune_stale_declarations` lists the stale `needs_tool` row, the stale `KNOWN_GAPS` row, and the ceiling dropping from 70 to 26.

B+C conflict because both insert at the same anchor; GK B rules on tool placement.
