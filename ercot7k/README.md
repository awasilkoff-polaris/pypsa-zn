# ercot7k - Texas7k full-cycle PSO case

A 6717-bus, 9140-branch, 634-injector synthetic ERCOT case (TAMU/Overbye's
**Texas7k**), converted to PSO-native CSV input tables. This is a **PSO-only**
case: it is driven straight into PSO through `aimmspy` (see
`../ercot7k_pso.py`) and never touches PyPSA.

## What's here

- `texas7k*.csv` (24 files, ~4.89 MB) - the full set of PSO input tables plus
  `texas7k.csv`, the options/control file (`SelectedDataFile` target).

These tables are copied byte-identical from the `ercot-public-dataset` repo's
`pso/texas7k_fullcycle/` case directory. The `results/` and `runs/`
subdirectories from that source are **not** included here - this case is
meant to be solved locally, not shipped with pre-solved output.

## The case

- **Network:** 6717 buses, 9140 branches, 634 injectors (generators).
- **Horizon:** `2018.04.09 00:00` -> `2018.04.16 00:00`, hourly (168 hours),
  per `texas7k_MDL_ID.csv`.
- **Cycle stack:** `SC` (security-constrained, 24 h lead) -> `DA`
  (day-ahead, 24 h lead) -> `RT` (real-time, 1 h), per `texas7k_CYC_ID.csv`.
- **Scale:** roughly 188 solves across the horizon / cycle stack, ~3-4
  minutes wall clock on PSO 3.3 (BETA 2026-07-14) + AIMMS 26.1.4.12 with
  CPLEX.
- **Results:** a full run writes ~47 result files, ~459 MB total. These are
  **not** committed to the repo - run the case locally and inspect them from
  the run's own results directory. Peak served load on the RT cycle is
  ~46,795 MW (this is a spring week; do not expect a summer-peak figure).

## Running it

See `../ercot7k_pso.py` at the repo root, and its `Prerequisites` / `Setup`
section in the top-level `README.md`. In short:

1. Copy `pso.local.toml.example` -> `pso.local.toml` at the repo root and
   fill in `project` (path to your PSO.aimms) and, for an academic AIMMS
   license, `license_url`.
2. `python ercot7k_pso.py`

## Attribution

The Texas7k network is TAMU/Overbye synthetic data
(electricgrids.engr.tamu.edu), "free for commercial or non-commercial use,"
with a requested registration + paper citation. Several other inputs (ARPA-E
PERFORM forecast/actual series, EIA, HIFLD) carry their own attribution
requirements, mostly CC-BY.

Full field-level provenance and the complete attribution block live in the
source dataset repo (`ercot-public-dataset`: `SOURCE.md`, `LICENSE-DATA.md`,
`pso/INPUT_SOURCES.md`) and are **not** duplicated here yet. They follow in a
separate change. Read them before publishing results or redistributing this
case.
