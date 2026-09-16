# toksearch_cmf

CMF provenance backend for [toksearch](https://github.com/GA-FDP/toksearch).
Records curated toksearch pipeline runs to the
[Common Metadata Framework](https://github.com/HewlettPackard/cmf).

This package owns every `cmflib` and `dvc` dependency; `toksearch` core knows
nothing about either. Core produces a `RunContext`
(`toksearch.provenance`) describing the run — shot source, signal
specifications, operation sequence, compute backend, git commit — and this
package consumes it.

## Install

`toksearch_cmf` is part of the `fdp-core` metapackage, so an environment
created with `fdp-install` already has it. Standalone:

```bash
conda install -c ga-fdp -c conda-forge toksearch_cmf
```

## Prerequisites

cmflib records the executing script's commit and hands output paths to DVC. The
script must therefore run from inside a git repository that has a remote and
DVC initialised, with the script itself committed there. `CmfRun` checks for
the repository up front rather than failing after a long compute.

Run it as `python -m fdp run python your_script.py`. In any environment carrying
cmflib, graphviz arrives transitively (`cmflib → dvc → pydot → graphviz`) and
installs its own layout engine at `bin/fdp`; `fdp` 0.6.0 declared graphviz as a
dependency so the installer's link order gives the FDP CLI the file back
(verified on pixi/rattler and micromamba 2.9.0). That still leaves two ways to
lose the collision: an `fdp` older than 0.6.0, or an installer whose link order
isn't guaranteed the way those two are. `python -m fdp` sidesteps the question
either way.

The technical claims here — the git+DVC requirement, and the graphviz story
with its two residual exposures — are kept deliberately in sync with
`toksearch`'s [Recording to CMF](https://ga-fdp.github.io/toksearch/latest/provenance/#recording-to-cmf)
prerequisites. The prose differs where local context demands it, so compare
the claims, not the wording; if you change a claim, change it in both.

## Use

```python
from toksearch_cmf import CmfRun

run = CmfRun("betan-ip-study", stage="assemble", work_dir=".")

results = pipeline.compute_multiprocessing(num_workers=8, provenance=run)

run.metrics("coverage", {"requested": len(shots), "returned": len(results)})
run.finalize()
```

Nothing there hand-writes a `cmflib.log_dataset` call. toksearch derives the
run description; `CmfRun` records it. Output directories declared with
`Pipeline.write` are picked up automatically; use `run.output(path, ...)` for
artifacts toksearch did not write itself.

Every execution carries `run`, the `CmfRun`'s own `run_id`, which is what a
chained pipeline's `parent_run` points at. To tag a run with something only
the caller knows, pass `properties=`:

```python
run = CmfRun("vloop-study", stage="d3d-fetch", work_dir=".",
             properties={"run_id": "20260916T120757"})
```

The keys land on the execution next to the ones derived from the run
context, so a multi-stage workflow that records many runs into one mlmd store
can select one run's executions by property rather than by artifact path.
Paths are not a reliable handle: cmflib identifies artifacts by content hash,
so a deterministic re-run that writes identical bytes is recorded against the
earlier run's path. Values must be scalars, and keys `CmfRun` records itself
are rejected at construction.

## Example

[`examples/betan_ip_peaks_cmf.py`](examples/betan_ip_peaks_cmf.py) is a
complete curated pipeline against real DIII-D data: per-shot βN and Ip peaks
from two different signal classes (`PtDataSignal` reads a PTData diagnostic,
`MdsSignal` reads an EFIT equilibrium quantity), written one netCDF file per
shot and recorded to a local CMF store.

## Documentation

See [Provenance and CMF](https://ga-fdp.github.io/toksearch/latest/provenance/)
in the TokSearch documentation for the full picture, including the
`toksearch.provenance` interface this package implements.
