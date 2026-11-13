"""Pre-solve repair of Link port frames a network missed (study harness #42).

PyPSA 1.1 keeps one Link attribute table for the whole process
(``n.components["Link"]["defaults"]``). Adding a Link with ``bus2`` to any
network adds ``bus2``, ``efficiency2`` and ``p2`` to that table, and only the
network that got the Link receives the matching columns and time-series frames.
A network built earlier, still resident in the app (``PyPSAService`` keeps one
per open project), then fails ``consistency_check`` on its next solve with
``KeyError: 'efficiency2'``; ``n.copy()`` does not repair it.

``ensure_link_port_frames`` adds what PyPSA would have added had the network
been built after the table grew: an empty float frame on ``n.snapshots`` for
each missing time-varying attribute and a column at its default for each missing
static one. It is idempotent and leaves a healthy network untouched.
"""

from __future__ import annotations

import pandas as pd


def ensure_link_port_frames(n) -> list[str]:
    """Add the Link columns and frames the shared attribute table names but
    ``n`` lacks. Returns what it added (``links.<attr>`` / ``links_t.<attr>``),
    ``[]`` for a healthy network."""
    c = n.components["Link"]
    defaults = c.defaults
    added = []
    for attr in defaults.index[defaults.static.astype(bool)]:
        if attr == "name" or attr in c.static.columns:
            continue
        c.static[attr] = defaults.at[attr, "default"]
        added.append(f"links.{attr}")
    for attr in defaults.index[defaults.varying.astype(bool)]:
        if attr in c.dynamic:
            continue
        c.dynamic[attr] = pd.DataFrame(index=n.snapshots, columns=c.static.index[:0], dtype=float)
        added.append(f"links_t.{attr}")
    return added
