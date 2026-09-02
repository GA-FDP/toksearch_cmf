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
import glob
import os
import subprocess
import tempfile
import unittest

import xarray as xr

from toksearch import Pipeline
from toksearch.backend.serial import SerialRecordSet
from toksearch.signal.mock_signal import MockSignal

from toksearch_cmf import CmfRun


def _cmf_workspace(path):
    """Build the workspace cmflib insists on, and return the working dir.

    Each step is load-bearing, and established empirically:

    * a git repo -- cmflib records the executing script's commit;
    * a git **remote** -- without one cmflib refuses to run at all, printing
      "*** Error git remote not set ***" and returning before doing anything;
    * `dvc init` **with** SCM, not `--no-scm` -- log_dataset ends up in
      `dvc add`, which wants DVC layered on the git repo;
    * a default DVC remote -- artifact hashing needs somewhere to point.
    """
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    remote = os.path.join(path, "remote.git")
    work = os.path.join(path, "work")
    os.makedirs(work)
    subprocess.run(["git", "init", "-q", "--bare", remote], check=True, env=env)
    subprocess.run(["git", "init", "-q", work], check=True, env=env)
    subprocess.run(["git", "-C", work, "remote", "add", "origin", remote],
                   check=True, env=env)
    with open(os.path.join(work, "run.py"), "w") as fh:
        fh.write("# curated pipeline\n")
    subprocess.run(["git", "-C", work, "add", "run.py"], check=True, env=env)
    subprocess.run(["git", "-C", work, "commit", "-qm", "init"], check=True, env=env)
    subprocess.run(["dvc", "init", "-q"], cwd=work, check=True, env=env)
    subprocess.run(["dvc", "remote", "add", "-d", "local",
                    os.path.join(path, "dvc-remote"), "-q"],
                   cwd=work, check=True, env=env)
    subprocess.run(["git", "-C", work, "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", work, "commit", "-qm", "dvc"], check=True, env=env)
    return work


def _pipeline(shots=(1, 2, 3)):
    pipeline = Pipeline(list(shots))
    pipeline.fetch_dataset("ds", {"ip": MockSignal()})
    return pipeline


def _artifact_names(work):
    from cmflib.cmfquery import CmfQuery

    return [str(a) for a in CmfQuery(os.path.join(work, "mlmd")).get_all_artifacts()]


class TestCmfIntegration(unittest.TestCase):
    """Exercises real cmflib, real DVC and a real Pipeline.write.

    The mocked unit tests cover the call shapes; these cover whether cmflib
    actually accepts them. An earlier defect -- output directories silently
    dropped because cmflib took a `dvc import-url` branch on absolute paths --
    passed every mocked test and only showed up here.
    """

    def _run(self, work, exist_ok=False, **kwargs):
        run = CmfRun("study", stage="assemble", work_dir=work, **kwargs)
        pipeline = _pipeline()
        pipeline.write(os.path.join(work, "peaks"), field="ds", fmt="netcdf",
                       exist_ok=exist_ok)
        results = pipeline.compute_serial(provenance=run)
        run.finalize()
        return run, results

    def test_pipeline_lands_in_mlmd(self):
        from cmflib.cmfquery import CmfQuery

        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            query = CmfQuery(os.path.join(work, "mlmd"))
            self.assertIn("study", query.get_pipeline_names())

    def test_one_execution_is_recorded(self):
        from cmflib.cmfquery import CmfQuery

        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            query = CmfQuery(os.path.join(work, "mlmd"))
            self.assertEqual(len(query.get_all_executions_in_pipeline("study")), 1)

    def test_the_execution_is_not_named_after_the_test_runner(self):
        # cmflib's log_dataset silently creates a context and execution named
        # from sys.argv[0] if none is open (cmf.py:744-752). If that fired, the
        # run would be filed under "pytest" rather than the declared stage.
        from cmflib.cmfquery import CmfQuery

        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            names = CmfQuery(os.path.join(work, "mlmd")).get_pipeline_names()
            self.assertEqual(names, ["study"])

    def test_the_inputs_artifact_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            self.assertTrue(any("inputs.json" in n for n in _artifact_names(work)))

    def test_the_output_directory_is_recorded_as_a_dvc_dir_hash(self):
        # The regression that mocks could not catch. A ".dir" suffix means DVC
        # hashed the directory -- the same mechanism the origin's per-shot
        # versioned store uses -- rather than the artifact being dropped.
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            peaks = [n for n in _artifact_names(work) if n.startswith("peaks:")]
            self.assertEqual(len(peaks), 1, _artifact_names(work))
            self.assertTrue(peaks[0].endswith(".dir"), peaks[0])

    def test_record_outcomes_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            self.assertTrue(any("record_outcomes" in n for n in _artifact_names(work)))

    def test_write_produces_one_file_per_shot(self):
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            out = os.path.join(work, "peaks")
            self.assertEqual(sorted(os.listdir(out)), ["1.nc", "2.nc", "3.nc"])

    def test_written_files_are_readable(self):
        # Per-file open, not open_mfdataset: dask is not installed here.
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            written = sorted(glob.glob(os.path.join(work, "peaks", "*.nc")))
            self.assertEqual(len(written), 3)
            merged = xr.concat(
                [xr.open_dataset(f) for f in written], dim="shot", data_vars="all"
            )
            self.assertIn("ip", merged)

    def test_two_identical_runs_share_an_input_identity(self):
        identities = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                work = _cmf_workspace(d)
                ctx = _pipeline()._run_context(SerialRecordSet, None)
                identities.append(ctx.input_identity())
        self.assertEqual(identities[0], identities[1])

    def test_two_identical_runs_share_one_input_artifact(self):
        # The point of inputs.json: same shots + same signals means CMF sees
        # one artifact, which is what connects the lineage graph.
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            self._run(work)
            # Same shots + same signals -> same inputs.json content; only the
            # output directory (irrelevant to input identity) needs a way
            # past Pipeline.write's non-empty-directory guard for the repeat.
            self._run(work, exist_ok=True)
            inputs = [n for n in _artifact_names(work) if "inputs.json" in n]
            self.assertEqual(len(inputs), 1, inputs)

    def test_chained_pipeline_records_the_parent_run(self):
        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            first_run, first = self._run(work)
            second = Pipeline(first)
            second.map(lambda rec: None)
            ctx = second._run_context(SerialRecordSet, None)
            self.assertEqual(ctx.parent_run, first_run.run_id)

    def test_a_failed_shot_is_counted_in_record_outcomes(self):
        from cmflib.cmfquery import CmfQuery

        def explode_on_two(rec):
            if rec.shot == 2:
                raise ValueError("no data")

        with tempfile.TemporaryDirectory() as d:
            work = _cmf_workspace(d)
            run = CmfRun("study", stage="assemble", work_dir=work)
            pipeline = _pipeline()
            pipeline.map(explode_on_two)
            pipeline.write(os.path.join(work, "peaks"), field="ds", fmt="netcdf")
            pipeline.compute_serial(provenance=run)
            run.finalize()
            # The output directory covers only the shots that succeeded; the
            # failure must still be visible in the record.
            self.assertEqual(sorted(os.listdir(os.path.join(work, "peaks"))),
                             ["1.nc", "3.nc"])
            self.assertTrue(
                any("record_outcomes" in n for n in _artifact_names(work))
            )


if __name__ == "__main__":
    unittest.main()
