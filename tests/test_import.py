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
    def test_imports(self):
        import toksearch_cmf  # noqa: F401

    def test_has_a_version(self):
        import toksearch_cmf

        self.assertTrue(toksearch_cmf.__version__)

    def test_toksearch_comes_from_a_resolved_package(self):
        # Inverted on 2026-09-03. This used to assert the opposite -- that
        # toksearch came from an editable ../toksearch sibling -- because no
        # *released* toksearch carried the provenance module this package
        # builds on. toksearch 2.11.0 ships it, so the sibling checkout is
        # gone and toksearch is an ordinary resolved dependency.
        #
        # Asserted via site-packages rather than a specific prefix so this
        # holds in both the pixi dev environment and the conda recipe test
        # environment. An editable install pointing at a sibling working tree
        # would not satisfy it, which is the thing worth catching: it would
        # silently test code that is not what the package depends on.
        import os

        import toksearch

        self.assertIn("site-packages", toksearch.__file__)
        self.assertFalse(
            os.path.realpath(toksearch.__file__).startswith(
                os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
                + os.sep
                + "toksearch"
                + os.sep
            ),
            f"toksearch resolved to a sibling checkout: {toksearch.__file__}",
        )

    def test_the_provenance_contract_is_importable(self):
        from toksearch.provenance import Provenance, RunContext  # noqa: F401

    def test_cmflib_resolves_from_conda(self):
        import cmflib  # noqa: F401

    def test_exports_cmf_run(self):
        from toksearch_cmf import CmfRun  # noqa: F401

    def test_cmf_run_is_a_provenance_backend(self):
        from toksearch.provenance import Provenance
        from toksearch_cmf import CmfRun

        self.assertTrue(issubclass(CmfRun, Provenance))
