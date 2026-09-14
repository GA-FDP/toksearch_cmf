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


# ---------------------------------------------------------------------
# B7b: archive_version stops being a placeholder.
#
# This module's own docstring has said since it was written that the field
# "carries the DVC directory hashes of the exact archive state that was
# read, and the input artifact becomes content-addressed all the way down"
# once the versioned store exists. It exists. This is that.
# ---------------------------------------------------------------------

import json as _json
import unittest as _unittest
from unittest import mock as _mock

from toksearch_cmf.inputs import UNVERSIONED, inputs_payload
from toksearch_cmf.snapshot import shards_for, snapshot_for_run


def _snapshot_ctx(catalog="catalog_20260907T232802Z", shots=(165920, 165921),
         signals=None):
    return _mock.Mock(
        source=_mock.Mock(to_dict=lambda: {"kind": "shotlist", "count": 2}),
        signals=signals if signals is not None else {
            "ip": {"class": "MdsSignal", "module": "toksearch.signal.mds",
                   "fields": {"treename": "efit01"}},
        },
        device="d3d",
        store={"catalog": catalog} if catalog else None,
        shots=shots,
    )


def _fake_shard_key(tree, shot):
    """Stands in for toksearch's shard_key.

    Patched rather than imported because this package's environment may hold
    a toksearch older than 2.14.0, where `store_path` does not exist. What is
    under test here is THIS module's use of the mapping -- dedup, span
    coverage, sorting -- not the mapping itself, which is toksearch's and is
    tested there.
    """
    return "%s-%d" % (tree, shot // 1_000_000)


class TestWhichShardsARunNeeds(_unittest.TestCase):
    """The tree->shard mapping is the caller's, and ptdata takes shard names.
    A tree crossing a million-shot boundary occupies TWO shards."""

    def setUp(self):
        patcher = _mock.patch("toksearch_cmf.snapshot.shard_key",
                              _fake_shard_key)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_tree_one_span_is_one_shard(self):
        self.assertEqual(shards_for(_snapshot_ctx()), ["efit01-0"])

    def test_one_tree_across_a_boundary_is_two_shards(self):
        self.assertEqual(shards_for(_snapshot_ctx(shots=(165920, 1165920))),
                         ["efit01-0", "efit01-1"])

    def test_several_trees_are_deduplicated_and_sorted(self):
        ctx = _snapshot_ctx(signals={
            "a": {"class": "MdsSignal", "fields": {"treename": "efit01"}},
            "b": {"class": "MdsSignal", "fields": {"treename": "bci"}},
            "c": {"class": "MdsSignal", "fields": {"treename": "efit01"}},
        })
        self.assertEqual(shards_for(ctx), ["bci-0", "efit01-0"])

    def test_signals_with_no_tree_contribute_nothing(self):
        ctx = _snapshot_ctx(signals={"p": {"class": "PtDataSignal", "fields": {}}})
        self.assertEqual(shards_for(ctx), [])


class TestTheSnapshotReachesInputsJson(_unittest.TestCase):
    def test_archive_version_holds_the_snapshot(self):
        snap = {"schema": "fdp-snapshot/1", "catalog": "catalog_X",
                "shots": [{"shot": 165920, "version": 2, "dir_hash": "a" * 32}],
                "shared": []}
        payload = inputs_payload(_snapshot_ctx(), archive_version=snap)
        self.assertEqual(payload["archive_version"]["catalog"], "catalog_X")

    def test_a_run_with_no_store_keeps_the_placeholder(self):
        # MAST reads no store. The placeholder must survive rather than
        # become an empty snapshot, which would read as "nothing was read".
        with _mock.patch("toksearch_cmf.snapshot.build_snapshot") as build:
            got = snapshot_for_run(_snapshot_ctx(catalog=None), store_root="")
        self.assertEqual(got, UNVERSIONED)
        build.assert_not_called()

    def test_two_catalogs_give_different_inputs_bytes(self):
        # Rule 8: the PHYSICAL identity. Same shots, same signals, different
        # catalog -- different bytes, so CMF sees different input artifacts.
        from toksearch.provenance.hashing import canonical_json
        a = canonical_json(inputs_payload(_snapshot_ctx(), archive_version={"catalog": "A"}))
        b = canonical_json(inputs_payload(_snapshot_ctx(), archive_version={"catalog": "B"}))
        self.assertNotEqual(a, b)

    def test_a_failure_to_build_degrades_to_the_placeholder(self):
        # Provenance must never take down the run it is recording.
        with _mock.patch("toksearch_cmf.snapshot.build_snapshot",
                         side_effect=RuntimeError("origin down")):
            self.assertEqual(snapshot_for_run(_snapshot_ctx(), store_root="/r"),
                             UNVERSIONED)


class TestItDegradesRatherThanCrashes(_unittest.TestCase):
    """Provenance must never take down the run it is recording.

    The package floor requires a toksearch carrying RunContext.store and
    .shots. This covers the install the solver was supposed to prevent --
    which is this project's recurring failure, not a hypothetical.
    """

    def test_a_run_context_without_store_records_unversioned(self):
        class OldContext:            # pre-B7a: no `store`, no `shots`
            signals = {}
        self.assertEqual(snapshot_for_run(OldContext(), store_root="/r"),
                         UNVERSIONED)

    def test_shards_for_survives_a_context_without_shots(self):
        class OldContext:
            signals = {"ip": {"fields": {"treename": "efit01"}}}
        self.assertEqual(shards_for(OldContext()), [])

    def test_no_ptdata_records_unversioned(self):
        with _mock.patch("toksearch_cmf.snapshot.build_snapshot", None):
            self.assertEqual(
                snapshot_for_run(_snapshot_ctx(), store_root="/r"),
                UNVERSIONED)
