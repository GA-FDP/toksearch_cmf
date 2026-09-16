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
import warnings
from typing import Any, Mapping, Optional, Sequence

from toksearch.provenance.base import Provenance

from .snapshot import snapshot_for_run
from .inputs import UNVERSIONED, write_inputs_file


#: Execution property keys that CmfRun fills from the RunContext and its own
#: state. A caller's ``properties`` may not use them: silently overwriting
#: ``source.hash`` or ``code.commit`` would corrupt the record that is the
#: whole point of this package.
RESERVED_PROPERTIES = frozenset({
    "run",
    "input_identity",
    "source.kind",
    "source.count",
    "source.hash",
    "backend.kind",
    "device",
    "parent_run",
    "code.commit",
    "code.dirty",
    "code.script",
    "ops",
})

#: Namespace under which _flatten records the backend's configuration.
BACKEND_CONFIG_PREFIX = "backend.config."

_SCALAR_TYPES = (str, int, float, bool)


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
        properties: Extra execution properties, recorded alongside the ones
            derived from the RunContext. Use them to tag a run with something
            only the caller knows -- a workflow's own run id, say, so that
            executions from different runs of a multi-stage workflow can be
            told apart in one mlmd store. Values must be scalars. Keys that
            CmfRun itself records are rejected here, at construction, rather
            than overwritten.
        strict: Raise instead of warning when a provenance hook fails.

    Every execution also carries ``run``: this object's ``run_id``. A chained
    pipeline records the previous run's id as ``parent_run``, and this is the
    property that id refers to.
    """

    def __init__(
        self,
        pipeline_name: str,
        stage: Optional[str] = None,
        work_dir: str = ".",
        inputs: Optional[Sequence[str]] = None,
        archive_version: str = UNVERSIONED,
        properties: Optional[Mapping[str, Any]] = None,
        strict: bool = False,
    ):
        self.pipeline_name = pipeline_name
        self.stage = stage or pipeline_name
        self.work_dir = os.path.abspath(work_dir)
        self.declared_inputs = list(inputs or [])
        self.archive_version = archive_version
        # Checked here, not in on_compute_start: safe_call turns a hook
        # exception into a warning unless strict is set, so a bad property
        # rejected there would be swallowed, no execution would be created,
        # and log_dataset would then file the run under sys.argv[0].
        self.properties = _check_properties(properties)
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
            custom_properties={
                "run": self.run_id,
                **self._flatten(ctx),
                **self.properties,
            },
        )

        # The placeholder becomes a saved snapshot when the run read a
        # versioned store: the exact version of every shot and shard, with
        # the hashes that let a third party check them. An explicit
        # archive_version= from the caller still wins.
        archive_version = self.archive_version
        if archive_version == UNVERSIONED:
            archive_version = snapshot_for_run(ctx)

        inputs_path = write_inputs_file(
            ctx,
            os.path.join(self.work_dir, "cmf_runs", self.run_id),
            archive_version=archive_version,
        )
        # _dvc_path on every log_dataset call, not just outputs: a file
        # survives cmflib's wrong branch by accident where a directory does
        # not, and depending on that accident is how the output bug hid.
        cmf.log_dataset(_dvc_path(inputs_path, self.work_dir), "INPUT")

        for path in self.declared_inputs:
            cmf.log_dataset(_dvc_path(path, self.work_dir), "INPUT")

    def on_compute_end(self, ctx, recordset) -> None:
        cmf = self._ensure_cmf()
        # Directories come from the pipeline definition, not by iterating the
        # recordset -- see RunContext.write_directories. record_outcomes does
        # iterate, unavoidably (it counts per-shot failures), which is why it
        # is called last: by then the results are needed anyway.
        for directory in ctx.write_directories():
            cmf.log_dataset(_dvc_path(directory, self.work_dir), "OUTPUT")
        cmf.log_execution_metrics("record_outcomes", record_outcomes(recordset))

    def output(self, *paths, **custom_properties) -> None:
        cmf = self._ensure_cmf()
        for path in paths:
            cmf.log_dataset(
                _dvc_path(path, self.work_dir), "OUTPUT",
                custom_properties=dict(custom_properties),
            )

    def metrics(self, name: str, values: dict) -> None:
        self._ensure_cmf().log_execution_metrics(name, dict(values))

    def finalize(self) -> None:
        if self._cmf is not None:
            self._cmf.finalize()


def _check_properties(properties) -> dict:
    checked = dict(properties or {})
    for key, value in checked.items():
        if key in RESERVED_PROPERTIES or key.startswith(BACKEND_CONFIG_PREFIX):
            raise ValueError(
                f"CmfRun property {key!r} is recorded by CmfRun itself and "
                f"cannot be overridden. Choose another key."
            )
        if not isinstance(value, _SCALAR_TYPES):
            raise TypeError(
                f"CmfRun property {key!r} is {type(value).__name__}; CMF "
                f"execution properties must be str, int, float or bool."
            )
    return checked


def _dvc_path(path, root: str) -> str:
    """Return a path cmflib can actually hand to DVC.

    cmflib's ``commit_output`` decides whether an artifact is already in the
    workspace with ``os.path.exists(os.getcwd() + '/' + folder)``
    (``cmflib/dvc_wrapper.py:297``) -- string concatenation, not
    ``os.path.join``. For an **absolute** path that builds ``/cwd//abs/path``,
    which never exists, so cmflib takes its ``dvc import-url --to-remote``
    branch instead of ``dvc add``. DVC then refuses a directory that is already
    in the workspace, cmflib swallows the exception, and **the artifact is
    silently not logged**.

    ``Pipeline.write`` stores an absolute directory (``_SafeWrite.__init__``
    calls ``os.path.abspath``), so every output directory hits this. Files
    happen to survive it -- the ``import-url`` branch succeeds for a path that
    does not exist relative to cwd -- which is why the failure shows up only
    for directories and is easy to miss.

    Passing the cwd-relative form takes cmflib down the ``dvc add`` branch,
    which is what it wanted all along, and yields a proper directory hash.
    Relative names are also more portable in the recorded metadata.

    The anchor is ``work_dir``, not ``os.getcwd()``. cmflib chdirs to
    ``cmf_init_path`` inside ``log_dataset`` and restores afterwards
    (``cmf.py:743``, ``cmf.py:881``), and ``cmf_init_path`` is derived from the
    ``filepath`` we pass -- ``work_dir/mlmd`` -- so it *is* ``work_dir``. Using
    cwd happens to work whenever the caller runs from ``work_dir`` and breaks
    silently otherwise.

    A path outside that root cannot be DVC-tracked from this repository at all,
    so it is returned unchanged with a warning rather than silently mangled.
    """
    text = str(path)
    root = os.path.abspath(root)
    if os.path.commonpath([os.path.abspath(text), root]) == root:
        return os.path.relpath(text, root)

    warnings.warn(
        f"Provenance artifact {text!r} is outside {root!r}, the directory "
        f"cmflib resolves DVC paths against, so DVC cannot track it and CMF "
        f"will not record its hash. Write outputs inside the repository to "
        f"have them recorded.",
        RuntimeWarning,
        stacklevel=3,
    )
    return text


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
