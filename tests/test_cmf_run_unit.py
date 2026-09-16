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
        # The logged path is relative to work_dir, which is what cmflib
        # resolves against -- so resolve it the same way to check existence.
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            run.on_compute_start(_ctx())
            args, _ = run._cmf.log_dataset.call_args
            self.assertTrue(os.path.exists(os.path.join(d, args[0])))

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
            self.assertIn(("peaks.nc", "INPUT"), logged)

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
            # Relative to work_dir, not the absolute path _SafeWrite stored.
            self.assertIn(("peaks", "OUTPUT"), logged)

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


class TestDvcPathRelativization(unittest.TestCase):
    """cmflib's commit_output checks `os.getcwd() + '/' + folder`
    (dvc_wrapper.py:297) -- string concatenation, not os.path.join. An
    absolute path builds '/cwd//abs/path', which never exists, so cmflib takes
    its `dvc import-url` branch instead of `dvc add`. DVC refuses a directory
    already in the workspace, cmflib swallows the error, and the artifact is
    silently not logged.

    Pipeline.write always stores an absolute directory, so every output hit
    this. Verified against real cmflib: before the fix a run recorded 3
    artifacts with no output directory; after it, 4, the output appearing as
    `peaks:<md5>.dir`.
    """

    def test_a_path_under_the_root_is_made_relative(self):
        from toksearch_cmf.run import _dvc_path

        self.assertEqual(_dvc_path("/a/b/peaks", "/a/b"), "peaks")

    def test_a_nested_path_is_made_relative(self):
        from toksearch_cmf.run import _dvc_path

        self.assertEqual(_dvc_path("/a/b/out/peaks", "/a/b"),
                         os.path.join("out", "peaks"))

    def test_a_path_outside_the_root_warns_rather_than_mangling(self):
        import warnings

        from toksearch_cmf.run import _dvc_path

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = _dvc_path("/somewhere/else/peaks", "/a/b")
        self.assertEqual(result, "/somewhere/else/peaks")
        self.assertEqual(len(caught), 1)
        self.assertIn("DVC cannot track", str(caught[0].message))

    def test_the_anchor_is_work_dir_not_cwd(self):
        """cmflib chdirs to cmf_init_path (= work_dir) inside log_dataset, so
        relativizing against cwd is only right when the caller happens to be
        running from work_dir. Run from elsewhere and cwd-anchoring breaks."""
        from toksearch.provenance.context import OpSpec

        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            out = os.path.join(d, "peaks")
            os.makedirs(out)
            run = CmfRun("study", work_dir=d)   # cwd is the repo, not d
            run._cmf = mock.MagicMock()
            ctx = _ctx()
            ctx_w = type(ctx)(
                source=ctx.source,
                ops=ctx.ops + (OpSpec("write", {"directory": out,
                                                "track": "directory"}),),
                signals=ctx.signals, backend=ctx.backend, code=ctx.code,
            )
            run.on_compute_start(ctx_w)
            run.on_compute_end(ctx_w, None)
            for call in run._cmf.log_dataset.call_args_list:
                self.assertFalse(os.path.isabs(call.args[0]),
                                 f"absolute path reached cmflib: {call.args[0]}")


class TestCallerProperties(unittest.TestCase):
    """Issue #4: a caller can attach its own execution properties.

    Validation lives in the constructor on purpose. toksearch's safe_call
    turns any hook exception into a warning unless strict=True, so a bad
    property rejected in on_compute_start would be swallowed, no execution
    would be created, and cmflib's log_dataset would then file the run under
    sys.argv[0]. Raising before the compute is the only unconditional path.
    """

    def _run(self, work_dir, **kwargs):
        run = CmfRun("study", stage="assemble", work_dir=work_dir, **kwargs)
        run._cmf = mock.MagicMock()
        return run

    def _execution_properties(self, run):
        run.on_compute_start(_ctx())
        _, kwargs = run._cmf.create_execution.call_args
        return kwargs["custom_properties"]

    def test_properties_default_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            self.assertEqual(CmfRun("study", work_dir=d).properties, {})

    def test_caller_properties_reach_the_execution(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d, properties={"run_id": "20260916T120757"})
            props = self._execution_properties(run)
            self.assertEqual(props["run_id"], "20260916T120757")

    def test_caller_properties_do_not_displace_the_run_context(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d, properties={"run_id": "x"})
            props = self._execution_properties(run)
            self.assertIn("input_identity", props)
            self.assertIn("code.commit", props)

    def test_a_reserved_key_is_rejected_at_construction(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            with self.assertRaises(ValueError) as caught:
                CmfRun("study", work_dir=d, properties={"source.kind": "mine"})
            self.assertIn("source.kind", str(caught.exception))

    def test_the_backend_config_namespace_is_reserved(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            with self.assertRaises(ValueError):
                CmfRun("study", work_dir=d,
                       properties={"backend.config.num_workers": 4})

    def test_the_run_key_is_reserved(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            with self.assertRaises(ValueError):
                CmfRun("study", work_dir=d, properties={"run": "mine"})

    def test_a_non_scalar_value_is_rejected_at_construction(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            with self.assertRaises(TypeError) as caught:
                CmfRun("study", work_dir=d, properties={"shots": [1, 2, 3]})
            self.assertIn("shots", str(caught.exception))

    def test_none_is_not_a_scalar(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            with self.assertRaises(TypeError):
                CmfRun("study", work_dir=d, properties={"note": None})

    def test_scalar_values_of_every_kind_are_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d, properties={"s": "x", "i": 1, "f": 0.5, "b": True})
            props = self._execution_properties(run)
            self.assertEqual((props["s"], props["i"], props["f"], props["b"]),
                             ("x", 1, 0.5, True))

    def test_the_runs_own_id_is_an_execution_property(self):
        # parent_run on a chained pipeline holds this id. Without this the
        # id appears only in the inputs.json path, and two identical runs
        # share one inputs.json artifact -- so the second run's id would be
        # recorded nowhere at all.
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            run = self._run(d)
            props = self._execution_properties(run)
            self.assertEqual(props["run"], run.run_id)

    def test_reserved_keys_cover_everything_flatten_emits(self):
        # The reserved set is what the constructor checks against; _flatten is
        # what actually fills the execution. If they drift, a caller key can
        # pass validation and then silently overwrite a RunContext key.
        from toksearch_cmf.run import RESERVED_PROPERTIES, BACKEND_CONFIG_PREFIX

        for key in CmfRun._flatten(_ctx()):
            self.assertTrue(
                key in RESERVED_PROPERTIES or key.startswith(BACKEND_CONFIG_PREFIX),
                key,
            )
