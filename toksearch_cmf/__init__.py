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
"""CMF provenance backend for toksearch.

Records curated toksearch pipeline runs to CMF. This package owns every
cmflib and DVC dependency; toksearch core knows nothing about either.

Usage::

    from toksearch_cmf import CmfRun

    results = pipeline.compute_multiprocessing(
        num_workers=16,
        provenance=CmfRun('betan-nbi-study', stage='assemble'),
    )
"""

from .run import CmfRun
from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions

__all__ = ["CmfRun"]
