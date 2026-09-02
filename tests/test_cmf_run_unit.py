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
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from toksearch import Pipeline
from toksearch.backend.serial import SerialRecordSet
from toksearch.signal.mock_signal import MockSignal

from toksearch_cmf import CmfRun


def _ctx():
    pipeline = Pipeline([1, 2, 3])
    pipeline.fetch("ip", MockSignal())
    return pipeline._run_context(SerialRecordSet, None)


def _git_repo(path):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "init", "-q", path], check=True, env=env)
    with open(os.path.join(path, "f.txt"), "w") as fh:
        fh.write("one\n")
    subprocess.run(["git", "-C", path, "add", "f.txt"], check=True, env=env)
    subprocess.run(["git", "-C", path, "commit", "-qm", "init"], check=True, env=env)


def _explode_on_shot_two(rec):
    if rec.shot == 2:
        raise ValueError("no data for this shot")


class TestCmfRunConstruction(unittest.TestCase):
    def test_requires_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError) as caught:
                CmfRun("study", work_dir=d)
            self.assertIn("git", str(caught.exception).lower())

    def test_the_git_error_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError) as caught:
                CmfRun("study", work_dir=d)
            self.assertIn("work_dir", str(caught.exception))

    def test_succeeds_inside_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            CmfRun("study", work_dir=d)

    def test_has_a_run_id(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            self.assertTrue(CmfRun("study", work_dir=d).run_id)

    def test_run_ids_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            self.assertNotEqual(CmfRun("a", work_dir=d).run_id,
                                CmfRun("b", work_dir=d).run_id)

    def test_stage_defaults_to_pipeline_name(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            self.assertEqual(CmfRun("study", work_dir=d).stage, "study")

    def test_strict_defaults_to_false(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            self.assertFalse(CmfRun("study", work_dir=d).strict)

    def test_no_cmflib_work_at_construction(self):
        # Constructing must be cheap and side-effect free: it happens before
        # the pipeline runs, and a Cmf() here would create mlmd state for a
        # run that may never happen.
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = CmfRun("study", work_dir=d)
            self.assertIsNone(run._cmf)


class TestCmfRunRecording(unittest.TestCase):
    """Exercise the hooks against a mocked cmflib, so no server is needed."""

    def _run(self, work_dir, **kwargs):
        run = CmfRun("study", stage="assemble", work_dir=work_dir, **kwargs)
        run._cmf = mock.MagicMock()
        return run

    def test_start_creates_context_and_execution(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            run._cmf.create_context.assert_called_once()
            run._cmf.create_execution.assert_called_once()

    def test_start_names_the_stage(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            _, kwargs = run._cmf.create_context.call_args
            self.assertEqual(kwargs["pipeline_stage"], "assemble")

    def test_context_and_execution_precede_any_log_dataset(self):
        # cmflib's log_dataset silently creates a context and execution named
        # after sys.argv[0] if none is open (cmf.py:744-752). If that fired,
        # the run would be filed under the script name, not the declared stage.
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            names = [c[0] for c in run._cmf.method_calls]
            self.assertLess(names.index("create_context"), names.index("log_dataset"))
            self.assertLess(names.index("create_execution"), names.index("log_dataset"))

    def test_start_logs_the_inputs_file(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            args, _ = run._cmf.log_dataset.call_args
            self.assertTrue(args[0].endswith("inputs.json"))
            self.assertEqual(args[1], "INPUT")

    def test_start_writes_a_real_inputs_file(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            args, _ = run._cmf.log_dataset.call_args
            self.assertTrue(os.path.exists(args[0]))

    def test_execution_properties_carry_the_input_identity(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            ctx = _ctx()
            run.on_compute_start(ctx)
            _, kwargs = run._cmf.create_execution.call_args
            self.assertEqual(kwargs["custom_properties"]["input_identity"],
                             ctx.input_identity())

    def test_execution_properties_carry_the_code_commit(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            _, kwargs = run._cmf.create_execution.call_args
            self.assertIn("code.commit", kwargs["custom_properties"])

    def test_execution_properties_are_all_scalars(self):
        # CMF custom_properties are scalar-valued; a nested dict or list would
        # be silently stringified or rejected depending on the backend.
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            _, kwargs = run._cmf.create_execution.call_args
            for key, value in kwargs["custom_properties"].items():
                self.assertIsInstance(value, (str, int, float, bool), key)

    def test_declared_inputs_are_logged_at_start(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            prior = os.path.join(d, "peaks.nc")
            with open(prior, "w") as fh:
                fh.write("x")
            run = self._run(d, inputs=[prior])
            run.on_compute_start(_ctx())
            logged = [(c.args[0], c.args[1]) for c in run._cmf.log_dataset.call_args_list]
            self.assertIn((prior, "INPUT"), logged)

    def test_declared_output_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            run._cmf.reset_mock()
            run.output(os.path.join(d, "fig.png"))
            args, _ = run._cmf.log_dataset.call_args
            self.assertEqual(args[1], "OUTPUT")

    def test_metrics_are_logged(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            run.metrics("eval", {"rmse": 0.1})
            run._cmf.log_execution_metrics.assert_called_once()

    def test_write_directories_are_logged_at_end(self):
        from toksearch.provenance.context import OpSpec

        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            out = os.path.join(d, "peaks")
            os.makedirs(out)
            run = self._run(d)
            ctx = _ctx()
            ctx_w = type(ctx)(
                source=ctx.source,
                ops=ctx.ops + (OpSpec("write", {"directory": out,
                                                "track": "directory"}),),
                signals=ctx.signals, backend=ctx.backend, code=ctx.code,
            )
            run.on_compute_start(ctx_w)
            run._cmf.reset_mock()
            run.on_compute_end(ctx_w, None)
            logged = [(c.args[0], c.args[1]) for c in run._cmf.log_dataset.call_args_list]
            self.assertIn((out, "OUTPUT"), logged)

    def test_end_does_not_iterate_the_recordset_for_paths(self):
        # Passing None as the recordset proves output directories came from the
        # context, not from iterating -- which on Ray/Spark would force the
        # whole compute as a side effect of recording provenance.
        from toksearch.provenance.context import OpSpec

        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            out = os.path.join(d, "peaks")
            os.makedirs(out)
            run = self._run(d)
            ctx = _ctx()
            ctx_w = type(ctx)(
                source=ctx.source,
                ops=ctx.ops + (OpSpec("write", {"directory": out,
                                                "track": "directory"}),),
                signals=ctx.signals, backend=ctx.backend, code=ctx.code,
            )
            run.on_compute_start(ctx_w)
            run.on_compute_end(ctx_w, None)  # must not raise

    def test_end_logs_record_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            run._cmf.reset_mock()
            pipeline = Pipeline([1, 2, 3])
            pipeline.fetch("ip", MockSignal())
            results = pipeline.compute_serial()
            run.on_compute_end(_ctx(), results)
            args, _ = run._cmf.log_execution_metrics.call_args
            self.assertEqual(args[0], "record_outcomes")
            self.assertEqual(args[1]["records"], 3)
            self.assertEqual(args[1]["failed"], 0)

    def test_failed_shots_are_counted_and_named(self):
        from toksearch_cmf.run import record_outcomes

        pipeline = Pipeline([1, 2, 3])
        pipeline.fetch("ip", MockSignal())
        pipeline.map(_explode_on_shot_two)
        results = pipeline.compute_serial()
        outcomes = record_outcomes(results)
        self.assertEqual(outcomes["failed"], 1)
        self.assertEqual(outcomes["failed_shots"], "2")

    def test_record_outcomes_handles_no_recordset(self):
        from toksearch_cmf.run import record_outcomes

        self.assertEqual(record_outcomes(None)["records"], 0)

    def test_finalize_calls_cmflib_finalize(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            run.finalize()
            run._cmf.finalize.assert_called_once()

    def test_finalize_without_a_start_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            CmfRun("study", work_dir=d).finalize()  # must not raise
