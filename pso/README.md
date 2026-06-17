# PSO engine for DevNet

Run the DevNet stress workflow with **PSO** (Power Systems Optimization, AIMMS)
as the solver instead of PyPSA, using **your own AIMMS install + license**. The
existing `devnet_stress.py` / menu / stress loop are unchanged — only the solver
swaps underneath.

## How it works

PyPSA stays the **data model + stress-transform library** (it builds the network
and applies the load / line / cost perturbations). PSO does the **solve**. The
swap happens at one seam in `lib/devnet_stress_lib.py`; everything downstream
(dashboards, plots, `index.html`) is untouched.

## Prerequisites

- **Python 3.10+** on PATH.
- **AIMMS installed** + a license (this is how you solve). The license can be the
  machine's configured license or an **academic/cloud license** (set via
  `license_url`). *We do not ship AIMMS or a license — you use yours.*
- Optional: [`uv`](https://docs.astral.sh/uv/) (setup is faster with it, but the
  setup script falls back to stock `python -m venv` + `pip`).

## Two environments (why)

`aimmspy` and PyPSA's optimizer have **conflicting dependencies** (aimmspy forces
`linopy` down and breaks `pypsa`'s solver), so they live in separate envs:

| env | holds | role |
|---|---|---|
| `.venv` | PyPSA stack | build/transform the network (+ native PyPSA workflow) |
| `.venv-pso` | `aimmspy` | drive PSO for the solve (called as a subprocess) |

The setup script creates both; you never manage them by hand.

## Setup

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```
```sh
# Linux / macOS
sh setup.sh
```

Then edit **`pso.local.toml`** (created from `pso.local.toml.example`):

```toml
engine  = "pso"
runner  = "local"
project = "C:/path/to/PSO.aimms"     # the PSO model we shipped you (encrypted is fine)
# license_url = "wss://licensing.aimms.cloud/..."   # only if you use an academic/cloud license
# python = "..."   # solve interpreter (.venv-pso) -- setup.ps1 fills this in
```

`aimmspy` is installed to match your AIMMS automatically; if your AIMMS is older,
set `aimms_version` (e.g. `"26.1"`) or `aimms_path` in the config.

## Verify

```powershell
.\.venv\Scripts\python.exe pso\doctor.py
```
The **doctor** checks PyPSA, the solve interpreter, aimmspy, the AIMMS install,
the license, and the model path — with a fix hint for each. Get it to `READY.`
before running.

## Run

```powershell
.\.venv\Scripts\python.exe devnet_stress.py
# choose "2) PSO" at the solver-engine prompt
```
Results are written exactly where DevNet always writes them (`stress_out/…`), so
the dashboards / plots / `index.html` work unchanged.

To force the engine without the menu: set `engine = "pso"` in `pso.local.toml`
(or `DEVNET_ENGINE=pso`). Default is `pypsa`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| doctor: `aimmspy` FAIL | re-run setup, or `uv pip install --python .venv-pso aimmspy` |
| doctor: `AIMMS install … missing` | install an AIMMS matching aimmspy's version, or set `aimms_version` / `aimms_path` |
| doctor: `PSO model … not found` | set `project = ".../PSO.aimms"` in `pso.local.toml` |
| results are all zeros (`Load=0`, penalty LMPs) | use **backslash** paths on unpatched PSO 3.3+Windows, or a path-fixed build; the doctor/run guards check a non-zero objective |
| `linopy` / `n.optimize` error | you're in the merged env — keep `.venv` (PyPSA) and `.venv-pso` (aimmspy) separate (re-run setup) |

## Docker fallback

If a deployment has **no AIMMS installed**, the self-contained Docker image is the
alternative (`runner = "docker"`, set `image` + `docker_args`). It carries AIMMS +
the encrypted model. See the AIMMS Docker notes. The Python path above is the lead
option when you already have AIMMS.
