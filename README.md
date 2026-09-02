# toksearch_cmf

CMF provenance backend for [toksearch](../toksearch). Records curated
toksearch pipeline runs to the Common Metadata Framework (CMF).

This package owns every `cmflib` and `dvc` dependency; `toksearch` core
knows nothing about either — it only produces a `RunContext`
(`toksearch.provenance`), which this package consumes.

Scaffold only: `CmfRun` is not implemented yet.
