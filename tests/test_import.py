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
import unittest


class TestPackage(unittest.TestCase):
    """Scaffold only. CmfRun arrives in a later task, and its export and
    Provenance-subclass tests come with it -- asserting them here would commit
    a package whose import raises."""

    def test_imports(self):
        import toksearch_cmf  # noqa: F401

    def test_has_a_version(self):
        import toksearch_cmf

        self.assertTrue(toksearch_cmf.__version__)

    def test_toksearch_comes_from_the_local_checkout(self):
        # The released conda toksearch has no provenance module. If this
        # resolves inside .pixi/envs, the environment is wrong and every later
        # task in this phase would be building against the wrong toksearch.
        import toksearch

        self.assertNotIn(".pixi/envs", toksearch.__file__)

    def test_the_provenance_contract_is_importable(self):
        from toksearch.provenance import Provenance, RunContext  # noqa: F401

    def test_cmflib_resolves_from_conda(self):
        import cmflib  # noqa: F401
