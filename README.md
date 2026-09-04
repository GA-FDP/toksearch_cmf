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

## Use

```python
from toksearch_cmf import CmfRun

run = CmfRun("betan-ip-study", stage="assemble", work_dir=".")

results = pipeline.compute_multiprocessing(num_workers=16, provenance=run)

run.metrics("coverage", {"requested": len(shots), "returned": len(results)})
run.finalize()
```

Nothing there hand-writes a `cmflib.log_dataset` call. toksearch derives the
run description; `CmfRun` records it. Output directories declared with
`Pipeline.write` are picked up automatically; use `run.output(path, ...)` for
artifacts toksearch did not write itself.

## Prerequisites

cmflib records the executing script's commit and hands output paths to DVC. The
script must therefore run from inside a git repository that has a remote and an
initialised DVC, with the script itself committed there. `CmfRun` checks for the
repository up front rather than failing after a long compute.

Run it as `python -m fdp run python your_script.py`. In any environment carrying
cmflib, graphviz arrives transitively (`cmflib → dvc → pydot → graphviz`) and
installs its own layout engine at `bin/fdp`; `fdp` 0.6.0 fixed the link order so
the FDP CLI keeps the file, but `python -m fdp` is unambiguous regardless.

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
