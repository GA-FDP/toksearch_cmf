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
import json
import os
import tempfile
import unittest

from toksearch import Pipeline
from toksearch.backend.serial import SerialRecordSet
from toksearch.signal.mock_signal import MockSignal

from toksearch_cmf.inputs import write_inputs_file


def _ctx(shots=(1, 2, 3), data=None):
    pipeline = Pipeline(list(shots))
    pipeline.fetch("ip", MockSignal(data=data) if data else MockSignal())
    return pipeline._run_context(SerialRecordSet, None)


class TestWriteInputsFile(unittest.TestCase):
    def test_creates_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(os.path.exists(write_inputs_file(_ctx(), d)))

    def test_is_named_inputs_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.basename(write_inputs_file(_ctx(), d)), "inputs.json")

    def test_creates_missing_directories(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "a", "b")
            self.assertTrue(os.path.exists(write_inputs_file(_ctx(), nested)))

    def test_contains_the_source(self):
        with tempfile.TemporaryDirectory() as d:
            with open(write_inputs_file(_ctx(), d)) as fh:
                self.assertEqual(json.load(fh)["source"]["kind"], "shotlist")

    def test_contains_the_signal_specs(self):
        with tempfile.TemporaryDirectory() as d:
            with open(write_inputs_file(_ctx(), d)) as fh:
                self.assertIn("ip", json.load(fh)["signals"])

    def test_contains_the_archive_version_slot(self):
        with tempfile.TemporaryDirectory() as d:
            with open(write_inputs_file(_ctx(), d)) as fh:
                self.assertEqual(json.load(fh)["archive_version"], "unversioned")

    def test_archive_version_is_overridable(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_inputs_file(_ctx(), d, archive_version="md5:abc.dir")
            with open(path) as fh:
                self.assertEqual(json.load(fh)["archive_version"], "md5:abc.dir")

    def test_identical_contexts_produce_identical_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(_ctx(), a), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(_ctx(), b), "rb") as fh:
                second = fh.read()
        self.assertEqual(first, second)

    def test_different_signals_produce_different_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(_ctx(), a), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(_ctx(data=[9, 9]), b), "rb") as fh:
                second = fh.read()
        self.assertNotEqual(first, second)

    def test_different_shots_produce_different_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(_ctx(shots=(1, 2)), a), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(_ctx(shots=(1, 2, 3)), b), "rb") as fh:
                second = fh.read()
        self.assertNotEqual(first, second)

    def test_shot_order_does_not_change_the_bytes(self):
        # RunContext hashes a sorted shot list; the same set in a different
        # order is the same input.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(_ctx(shots=(3, 1, 2)), a), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(_ctx(shots=(1, 2, 3)), b), "rb") as fh:
                second = fh.read()
        self.assertEqual(first, second)

    def test_excludes_code_backend_and_ops(self):
        # Two runs over identical data share an input artifact even when the
        # code, backend or operations differ. Including any of them here would
        # break that, and with it the lineage graph's connectivity.
        with tempfile.TemporaryDirectory() as d:
            with open(write_inputs_file(_ctx(), d)) as fh:
                payload = json.load(fh)
            for excluded in ("code", "backend", "ops"):
                self.assertNotIn(excluded, payload)

    def test_backend_does_not_change_the_bytes(self):
        from toksearch.backend.multiprocessing import (
            MultiprocessingConfig,
            MultiprocessingRecordSet,
        )

        pipeline = Pipeline([1, 2, 3])
        pipeline.fetch("ip", MockSignal())
        serial = pipeline._run_context(SerialRecordSet, None)
        mp = pipeline._run_context(
            MultiprocessingRecordSet, MultiprocessingConfig(num_workers=4)
        )
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(serial, a), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(mp, b), "rb") as fh:
                second = fh.read()
        self.assertEqual(first, second)

    def test_payload_matches_the_run_context_input_identity(self):
        # The file's content and RunContext.input_identity() must agree on what
        # "the same input" means, or dedupe and the recorded identity diverge.
        from toksearch.provenance.hashing import sha256_of
        from toksearch_cmf.inputs import inputs_payload

        ctx = _ctx()
        payload = inputs_payload(ctx)
        self.assertEqual(
            sha256_of({k: payload[k] for k in ("source", "signals", "device")}),
            ctx.input_identity(),
        )


class TestArchiveVersionIsOutsideTheIdentity(unittest.TestCase):
    """archive_version deliberately sits in the file but not the identity.

    input_identity() answers "which shots, which signals"; the file's content
    answers "which exact bytes". They coincide only while archive_version is
    constant. Pinning the asymmetry so nobody collapses the two later.
    """

    def test_archive_version_changes_the_file(self):
        ctx = _ctx()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(write_inputs_file(ctx, a, archive_version="unversioned"), "rb") as fh:
                first = fh.read()
            with open(write_inputs_file(ctx, b, archive_version="md5:abc.dir"), "rb") as fh:
                second = fh.read()
        self.assertNotEqual(first, second)

    def test_archive_version_does_not_change_input_identity(self):
        # RunContext knows nothing about archives, by design.
        ctx = _ctx()
        before = ctx.input_identity()
        with tempfile.TemporaryDirectory() as d:
            write_inputs_file(ctx, d, archive_version="md5:abc.dir")
        self.assertEqual(ctx.input_identity(), before)

    def test_archive_version_is_not_in_the_identity_fields(self):
        from toksearch_cmf.inputs import inputs_payload

        payload = inputs_payload(_ctx(), archive_version="md5:abc.dir")
        self.assertIn("archive_version", payload)
        self.assertNotIn("archive_version", ("source", "signals", "device"))
