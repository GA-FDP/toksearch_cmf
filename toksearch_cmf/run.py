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
"""CmfRun -- the CMF implementation of toksearch's Provenance interface.

Every cmflib call in this module runs on the driver process, after
``compute_*`` has returned. That is not a convention to remember, it is the
only place these methods are called from: ``Pipeline.write`` runs in workers
but only records paths, and this class reads those paths afterwards. cmflib
therefore never enters a forked child, where the xrdcl-pelican curl worker
pool would be dead.
"""

import os
import subprocess
import uuid
from typing import Optional, Sequence

from toksearch.provenance.base import Provenance

from .inputs import UNVERSIONED, write_inputs_file


class CmfRun(Provenance):
    """Record a curated toksearch run to CMF.

    Arguments:
        pipeline_name: The CMF pipeline this run belongs to. Choose a durable,
            meaningful name -- it is how the run is found later.
        stage: The CMF pipeline stage. Defaults to pipeline_name.
        work_dir: Directory holding the mlmd store and this run's scratch
            files. Must be inside a git repository: cmflib records the
            executing script's commit and cannot work without one.
        inputs: Paths to artifacts produced by *earlier, separate* runs that
            this run consumes. In-process chaining needs nothing here -- a
            Pipeline built on a previous RecordSet is linked automatically via
            RunContext.parent_run.
        archive_version: Version identifier of the raw archive state read.
            Left "unversioned" until the origin per-shot versioned store
            exists.
        strict: Raise instead of warning when a provenance hook fails.
    """

    def __init__(
        self,
        pipeline_name: str,
        stage: Optional[str] = None,
        work_dir: str = ".",
        inputs: Optional[Sequence[str]] = None,
        archive_version: str = UNVERSIONED,
        strict: bool = False,
    ):
        self.pipeline_name = pipeline_name
        self.stage = stage or pipeline_name
        self.work_dir = os.path.abspath(work_dir)
        self.declared_inputs = list(inputs or [])
        self.archive_version = archive_version
        self.strict = strict
        self.run_id = uuid.uuid4().hex

        self._require_git_repo()

        self._cmf = None

    def _require_git_repo(self):
        """Fail now, not after a long compute, if git is missing.

        cmflib logs the executing script's commit. Without a repository it
        fails deep inside logging, after the expensive part has already run.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(
                f"CmfRun could not run git in {self.work_dir!r}: {e}. "
                f"cmflib records the executing script's git commit, so a "
                f"curated run must live in a git repository. Pass work_dir= "
                f"pointing at one."
            )

        if result.returncode != 0 or result.stdout.strip() != "true":
            raise RuntimeError(
                f"CmfRun requires a git repository, but {self.work_dir!r} is "
                f"not inside one. cmflib records the executing script's git "
                f"commit. Move the script into a git repo, or pass work_dir= "
                f"pointing at one."
            )

    def _ensure_cmf(self):
        if self._cmf is None:
            from cmflib.cmf import Cmf

            self._cmf = Cmf(
                filepath=os.path.join(self.work_dir, "mlmd"),
                pipeline_name=self.pipeline_name,
            )
        return self._cmf

    @staticmethod
    def _flatten(ctx) -> dict:
        """Flatten a RunContext into CMF custom_properties (scalar values).

        Note what is *not* here: the physical input identity. That lives on the
        inputs.json artifact, whose DVC hash CMF computes itself. This records
        the logical identity -- which shots, which signals -- and the two
        deliberately answer different questions. See inputs.py.
        """
        code = ctx.code
        properties = {
            "input_identity": ctx.input_identity(),
            "source.kind": ctx.source.kind,
            "source.count": ctx.source.count,
            "source.hash": ctx.source.hash,
            "backend.kind": ctx.backend.kind,
            "device": ctx.device or "",
            "parent_run": ctx.parent_run or "",
            "code.commit": code.commit or "",
            "code.dirty": str(code.dirty),
            "code.script": code.script or "",
            "ops": ",".join(op.op for op in ctx.ops),
        }
        for key, value in ctx.backend.config.items():
            properties[f"backend.config.{key}"] = str(value)
        return {k: v for k, v in properties.items() if v is not None}

    def on_compute_start(self, ctx) -> None:
        cmf = self._ensure_cmf()

        # Context and execution first: cmflib's log_dataset silently creates
        # both, named from sys.argv[0], if none is open (cmf.py:744-752).
        cmf.create_context(pipeline_stage=self.stage)
        cmf.create_execution(
            execution_type=self.stage,
            custom_properties=self._flatten(ctx),
        )

        inputs_path = write_inputs_file(
            ctx,
            os.path.join(self.work_dir, "cmf_runs", self.run_id),
            archive_version=self.archive_version,
        )
        cmf.log_dataset(inputs_path, "INPUT")

        for path in self.declared_inputs:
            cmf.log_dataset(path, "INPUT")

    def on_compute_end(self, ctx, recordset) -> None:
        cmf = self._ensure_cmf()
        # Directories come from the pipeline definition, not by iterating the
        # recordset -- see RunContext.write_directories. record_outcomes does
        # iterate, unavoidably (it counts per-shot failures), which is why it
        # is called last: by then the results are needed anyway.
        for directory in ctx.write_directories():
            cmf.log_dataset(directory, "OUTPUT")
        cmf.log_execution_metrics("record_outcomes", record_outcomes(recordset))

    def output(self, *paths, **custom_properties) -> None:
        cmf = self._ensure_cmf()
        for path in paths:
            cmf.log_dataset(
                str(path), "OUTPUT", custom_properties=dict(custom_properties)
            )

    def metrics(self, name: str, values: dict) -> None:
        self._ensure_cmf().log_execution_metrics(name, dict(values))

    def finalize(self) -> None:
        if self._cmf is not None:
            self._cmf.finalize()


def record_outcomes(recordset) -> dict:
    """Summarize which shots succeeded, so partial results are never silent.

    A shot that errored wrote no file, so an output directory's hash covers
    only the shots that succeeded. That is correct, but it must be *visible*:
    without this, a run that quietly lost half its shots is indistinguishable
    from one that was asked for half as many.
    """
    if recordset is None:
        return {"records": 0, "failed": 0, "failed_shots": ""}

    failed = [rec.shot for rec in recordset if rec.get("errors", None)]
    return {
        "records": len(recordset),
        "failed": len(failed),
        # Truncated: CMF properties are scalars, and a run can fail thousands
        # of shots. The count above is always exact.
        "failed_shots": ",".join(str(s) for s in sorted(failed)[:100]),
    }
