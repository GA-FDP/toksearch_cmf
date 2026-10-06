# Copyright 2026 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The saved snapshot a recorded run read.

`inputs.py` has said since it was written that `archive_version` would one
day carry "the DVC directory hashes of the exact archive state that was
read", making the input artifact content-addressed all the way down. This is
that: a saved snapshot, built from what the `RunContext` already knows.

It preserves the two-level split `inputs.py` documents:

    input_identity()      which shots, which signals   -- LOGICAL
    inputs.json content   which exact bytes            -- PHYSICAL

The snapshot lives only in the second. Two runs over the same shots at
different catalogs are the same logical input and different physical ones,
and cmflib dedupes on the file, so it sees the stronger notion.
"""

import os

from .inputs import UNVERSIONED

#: A shard covers a million-shot span. Imported rather than redefined: the
#: mapping from a tree to a shard is MDSplus knowledge and toksearch owns it.
#: A second copy is how the `ical` table drifted and silently returned the
#: wrong calibration.
try:
    from toksearch.signal.store_path import shard_key
except ImportError:  # toksearch older than 2.14.0
    shard_key = None

#: Bound at module level, not imported inside the function, so it can be
#: replaced in tests and so an install without ptdata degrades to the
#: placeholder rather than raising. ptdata is not a hard dependency here:
#: a device with no store (MAST) records provenance perfectly well without
#: one.
try:
    from ptdata import build_snapshot
except ImportError:
    build_snapshot = None


def _treenames(ctx):
    """Tree names behind a run's signals.

    `ctx.signals` records each signal's class, module and fields, and an
    MdsSignal's fields carry `treename` -- so no new RunContext field is
    needed. A signal with no tree (PtDataSignal, MastSignal) contributes
    nothing.
    """
    out = set()
    for spec in (ctx.signals or {}).values():
        tree = (spec.get("fields") or {}).get("treename")
        if tree:
            out.add(tree)
    return out


def shards_for(ctx):
    """The shared shards a run touches, sorted.

    One entry per (tree, million-shot span). A run crossing a boundary
    occupies TWO shards for one tree, and recording only the first would
    leave the snapshot leaning on its catalog for half its model trees --
    silently, and only for runs that happen to span a boundary.
    """
    shots = getattr(ctx, "shots", None)
    if shard_key is None or not shots:
        return []
    return sorted({shard_key(tree, shot)
                   for tree in _treenames(ctx)
                   for shot in shots})


def snapshot_for_run(ctx, store_root=None):
    """Describe what this run reads, or UNVERSIONED when there is no store.

    Never raises. Provenance must not take down the run it is recording, and
    a snapshot that could not be built is a gap in the record rather than a
    reason to lose the results. The placeholder survives, which reads
    honestly as "this was not versioned".
    """
    # getattr, not attribute access: the package floor requires a toksearch
    # that has these, but a provenance layer must degrade rather than take
    # down the run it is recording -- and this project's recurring failure is
    # precisely the mixed install a solver was supposed to prevent.
    store = getattr(ctx, "store", None) or {}
    shots = getattr(ctx, "shots", None)

    catalog = store.get("catalog")
    root = store_root if store_root is not None else os.environ.get(
        "FDP_STORE_ROOT", "")
    if not catalog or not root or not shots:
        return UNVERSIONED

    if build_snapshot is None:
        return UNVERSIONED

    try:
        return build_snapshot(root, shots=list(shots),
                              shards=shards_for(ctx), catalog=catalog,
                              sql_snapshots=store.get("sql_snapshots") or None)
    except Exception:  # noqa: BLE001 - see the docstring
        return UNVERSIONED
