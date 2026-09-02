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
"""The input artifact for a recorded run.

cmflib's ``log_dataset`` hashes a real path on disk (it calls ``commit_output``
then ``dvc_get_hash``), so an input artifact cannot be a synthetic URI. This
module writes a real file whose *content hash is the input identity*: two runs
reading the same shots with the same signals produce byte-identical files, so
CMF recognizes them as the same artifact and the lineage graph connects.

Only what determines *which data is read* goes in. Code version, backend, and
operations belong on the execution, not on the input artifact -- otherwise two
runs over identical data would never share an input.
"""

import os

from toksearch.provenance.hashing import canonical_json

INPUTS_FILENAME = "inputs.json"

#: Placeholder until the origin per-shot versioned store exists. Once it does,
#: this carries the DVC directory hashes of the exact archive state that was
#: read, and the input artifact becomes content-addressed all the way down.
#:
#: NOTE the deliberate asymmetry: ``archive_version`` is written into the file
#: but is NOT part of ``RunContext.input_identity()``, which toksearch computes
#: from source + signals + device alone and knows nothing about archives. The
#: two therefore answer different questions:
#:
#:   input_identity()      "which shots, which signals"  -- logical input
#:   inputs.json content   "which exact bytes"           -- physical input
#:
#: While archive_version is constant they coincide. Once the versioned store
#: fills it in they will not, and that is correct: two runs over the same shots
#: against different archive states are the same logical input and different
#: physical inputs. CMF dedupes on the file, so it sees the stronger notion.
#: Do not "fix" this by folding archive_version into input_identity -- that
#: would require RunContext to know about archives, which is exactly the
#: coupling the toksearch/toksearch_cmf split exists to avoid.
UNVERSIONED = "unversioned"


def inputs_payload(ctx, archive_version: str = UNVERSIONED) -> dict:
    """Build the deterministic input description for a RunContext."""
    return {
        "source": ctx.source.to_dict(),
        "signals": ctx.signals,
        "device": ctx.device,
        "archive_version": archive_version,
    }


def write_inputs_file(
    ctx, directory: str, archive_version: str = UNVERSIONED
) -> str:
    """Write inputs.json into directory and return its path.

    The file is written with canonical JSON so identical inputs give identical
    bytes on any machine.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, INPUTS_FILENAME)
    with open(path, "w") as fh:
        fh.write(canonical_json(inputs_payload(ctx, archive_version)))
    return path
