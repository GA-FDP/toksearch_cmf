#!/usr/bin/env python
"""Curated pipeline: per-shot betaN and Ip peaks, recorded to CMF.

Demonstrates the full provenance loop on real DIII-D data. toksearch derives
the run description itself -- shot list, signal specs, operation sequence,
backend, git commit -- and CmfRun records it; nothing here hand-writes a
cmflib log_dataset call.

Two different signal classes on purpose: PtDataSignal reads a PTData
diagnostic, MdsSignal reads an EFIT equilibrium quantity from an MDSplus tree.
Their Signal.spec() output is what makes the input artifact identity.

Running it
----------
cmflib needs a git repository with a remote and an initialised DVC, and it
records the executing script's commit -- so this must run from inside such a
repository, with the script committed there.

    python -m fdp run python betan_ip_peaks_cmf.py

`python -m fdp`, not `fdp run`: graphviz installs its layout engine at the same
bin/fdp path, so in any environment carrying cmflib (cmflib -> dvc -> pydot ->
graphviz) `fdp run` may invoke a graph layout tool instead. See
docs/2026-09-02-fdp-cli-rename.md.

Writes one netCDF file per shot under ./peaks/ and records the run in local
mlmd. Read the results back with:

    import glob, xarray as xr
    ds = xr.concat([xr.open_dataset(f) for f in sorted(glob.glob('peaks/*.nc'))],
                   dim='shot', data_vars='all')
"""

import numpy as np
import pandas as pd
import xarray as xr

from toksearch import MdsSignal, Pipeline
from toksearch_d3d import PtDataSignal
from toksearch_d3d.sql import connect_d3drdb
from toksearch_cmf import CmfRun

SHOT_QUERY = """
    SELECT s.shot
    FROM shots s
    JOIN shots_type t ON s.shot = t.shot
    WHERE t.shot_type = 'plasma'
      AND s.entered BETWEEN '2024-06-01' AND '2024-06-08'
    ORDER BY s.shot
"""


def peaks(rec):
    """Reduce the fetched time series to the scalars worth keeping."""
    ds = xr.Dataset(coords={"shot": ("shot", [rec.shot])})

    ip = rec.get("ip", None)
    if ip is not None:
        # PtData returns amps; MA is the useful unit here.
        values = np.asarray(ip["data"], dtype=float)
        ds["ip_max_ma"] = ("shot", [float(np.nanmax(np.abs(values))) / 1e6])

    betan = rec.get("betan", None)
    if betan is not None:
        values = np.asarray(betan["data"], dtype=float)
        # Values >= 10 are known unphysical artifacts in this quantity.
        values = values[values < 10]
        ds["betan_max"] = (
            "shot", [float(np.nanmax(values)) if values.size else np.nan]
        )

    rec["peaks"] = ds


def main():
    with connect_d3drdb() as conn:
        shots = pd.read_sql(SHOT_QUERY, conn)["shot"].tolist()
    print(f"{len(shots)} plasma shots")

    run = CmfRun("betan-ip-study", stage="assemble", work_dir=".")

    pipeline = Pipeline(shots)
    pipeline.fetch("ip", PtDataSignal("ip"))
    pipeline.fetch(
        "betan",
        MdsSignal(r"\betan", "efit01", location="remote://atlas.gat.com"),
    )
    pipeline.map(peaks)
    pipeline.keep(["peaks"])
    # A shot that failed any earlier step writes no file, so the directory --
    # and the DVC hash CMF takes over it -- covers exactly the shots that
    # completed. record_outcomes carries the failure count.
    pipeline.write("peaks", field="peaks", fmt="netcdf")

    results = pipeline.compute_multiprocessing(num_workers=8, provenance=run)

    failed = [r.shot for r in results if r.get("errors", None)]
    print(f"{len(results)} records, {len(failed)} failed")
    run.metrics("coverage", {"requested": len(shots), "returned": len(results)})
    run.finalize()


if __name__ == "__main__":
    main()
