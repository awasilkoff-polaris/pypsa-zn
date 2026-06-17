#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# pso_engine.py -- in-repo engine swap: solve a DevNet PyPSA network with PSO.
#
# Lets the existing DevNet setup (devnet_stress.py / menu / stress loop) keep
# using PyPSA as the data model + stress-transform library, but solve with PSO
# instead of PyPSA's LP. devnet_stress_lib routes solve_with_duals + collect_results
# here when DEVNET_ENGINE=pso.
#
# Flow: live (stress-mutated) PyPSA network -> PSO case (pso_case_writer) ->
# run PSO via aimmspy in a subprocess (the separate solve interpreter, so PyPSA
# and AIMMS stay in separate envs) -> read results (pso_to_results.map_results) ->
# return a dict in the exact shape collect_results produces.
#
# Two run backends, selected by DEVNET_PSO_RUNNER:
#   local  (default) -- aimmspy in the configured solve interpreter (.venv-pso),
#                       against the client's PSO model + their own AIMMS license.
#   docker           -- the self-contained PSO baked image. No AIMMS/license/conda
#                       on the runner; the container carries everything. Sets
#                       OS=Linux (required by PSO 3.3 in-container) and uses the
#                       baked image's run contract: -v <case>:/in -v <out>:/out
#                       <image> /in/<master>.csv  (reads sibling _*.csv, writes
#                       results_*.csv to /out).
#
# Config via env vars (inherited by the run_pso.py subprocess for the local path):
#   DEVNET_PSO_RUNNER        local | docker          (default: local)
#   DEVNET_PSO_SLACK         slack bus name          (default: first bus)
#   DEVNET_PSO_KEEPTMP       keep the per-solve temp case dir
#   --- local (aimmspy) backend -> read by run_pso.py ---
#   DEVNET_PSO_PYTHON        python.exe with aimmspy (default: current interpreter)
#   DEVNET_PSO_PROJECT       full path to PSO.aimms  (the client's PSO model)
#   DEVNET_PSO_AIMMS_PATH    explicit AIMMS /Bin path (optional)
#   DEVNET_PSO_AIMMS_VERSION AIMMS version for find_aimms_path() (optional)
#   DEVNET_PSO_LICENSE_URL   academic/cloud license URL (wss://...) (optional)
#   --- docker backend ---
#   DEVNET_PSO_IMAGE         docker image tag        (default: pso-pso33:3.3)
#   DEVNET_PSO_DOCKER        docker executable       (default: docker)
#   DEVNET_PSO_DOCKER_ARGS   extra docker run args (e.g. license volume + MAC)
#
# NOTE (performance): every solve opens AIMMS fresh (~tens of seconds) in BOTH
# backends -- a `docker run` is one-shot, and the local path re-opens the project
# each call. Fine for single/preview runs and short sweeps. For high-throughput
# sweeps, a persistent aimmspy session (open once, StartupDataID per case) is the
# future optimization -- the one place the local backend could outrun docker.

import os
import sys
import shlex
import shutil
import tempfile
import subprocess
from pathlib import Path

import numpy as np

PSO_DIR = Path(__file__).resolve().parent.parent / "pso"
sys.path.insert(0, str(PSO_DIR))
from pso_case_writer import write_pso_case          # noqa: E402
from pso_to_results import map_results               # noqa: E402

# Default to the CURRENT interpreter -- with `uv sync --extra pso`, this same
# .venv has both PyPSA and aimmspy, so no separate env is needed. Override with
# DEVNET_PSO_PYTHON only if aimmspy lives in a different interpreter.
AIMMSPYTHON_PY = os.environ.get("DEVNET_PSO_PYTHON", sys.executable)
RUN_PSO = PSO_DIR / "run_pso.py"
HORIZON = {"start": "2012.01.02 00:00", "interval_hours": 1}

RUNNER = os.environ.get("DEVNET_PSO_RUNNER", "local").lower()      # local | docker
PSO_IMAGE = os.environ.get("DEVNET_PSO_IMAGE", "pso-pso33:3.3")    # 3.3 baked image (tag TBD)
DOCKER_BIN = os.environ.get("DEVNET_PSO_DOCKER", "docker")


def _snapshot_load(n):
    """Effective per-bus load at the single snapshot (static or time-series)."""
    snap = n.snapshots[0]
    loads = {}
    has_ts = (hasattr(n, "loads_t") and getattr(n.loads_t, "p_set", None) is not None
              and len(n.loads_t.p_set))
    for ld, r in n.loads.iterrows():
        if has_ts and ld in n.loads_t.p_set.columns:
            p = float(n.loads_t.p_set.loc[snap, ld])
        else:
            p = float(r["p_set"])
        loads[ld] = (r["bus"], p)
    by_bus = {}
    for _, (bus, p) in loads.items():
        by_bus[bus] = by_bus.get(bus, 0.0) + p
    return by_bus


def network_to_case(n, out_dir, prefix="case"):
    """Write a PSO case from a live (already stress-transformed) PyPSA network."""
    buses = list(n.buses.index)
    slack = os.environ.get("DEVNET_PSO_SLACK", buses[0])

    branches = [
        {"name": ln, "fr": r["bus0"], "to": r["bus1"],
         "x": float(r["x"]), "r": 0.0, "limit": float(r["s_nom"])}
        for ln, r in n.lines.iterrows()
    ]
    injectors = [
        {"name": g, "node": r["bus"], "maxmw": float(r["p_nom"]),
         "cost": float(r["marginal_cost"]), "loadflag": 0}
        for g, r in n.generators.iterrows()
    ]
    node_loads = _snapshot_load(n)

    return write_pso_case(
        out_dir, prefix,
        buses=buses, branches=branches, injectors=injectors,
        node_loads=node_loads, total_load=sum(node_loads.values()),
        slack_bus=slack, horizon=HORIZON,
        pso_version=os.environ.get("DEVNET_PSO_VERSION", "3.2"),
    )


def _run_local(control, out_dir):
    """aimmspy backend: drive run_pso.py in the solve interpreter (local AIMMS)."""
    return subprocess.run(
        [AIMMSPYTHON_PY, str(RUN_PSO), str(control), str(out_dir / "results.csv")],
        capture_output=True, text=True,
    )


def _run_docker(in_dir, out_dir, master_name):
    """
    docker backend: the shippable PSO 3.3 baked image (OS=Linux required).

    DEVNET_PSO_DOCKER_ARGS carries any licensing args needed by the image variant,
    spliced in before the image. For the customer-activated nodelock image
    (pso-pso33:3.3-nodelock) that is the activated license volume + pinned MAC, e.g.
        DEVNET_PSO_DOCKER_ARGS="--mac-address 02:42:ac:11:00:02 -v pso_nodelock:/data"
    A baked-keyless image needs none.
    """
    extra = shlex.split(os.environ.get("DEVNET_PSO_DOCKER_ARGS", ""))
    cmd = [
        DOCKER_BIN, "run", "--rm",
        "-e", "OS=Linux",                       # PSO 3.3 in-container needs this
        "-v", f"{in_dir}:/in",
        "-v", f"{out_dir}:/out",
        *extra,
        PSO_IMAGE, f"/in/{master_name}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def solve_network(n) -> dict:
    """
    Solve `n` with PSO and return a results dict matching
    devnet_stress_lib.collect_results: {snapshot, objective, dc_dispatch_mw,
    lmp, line_loading_pu} (+ status). Backend chosen by DEVNET_PSO_RUNNER.
    """
    tmp = Path(tempfile.mkdtemp(prefix="pso_engine_"))
    in_dir, out_dir = tmp / "in", tmp / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        control = network_to_case(n, in_dir, prefix="case")  # in_dir/case.csv + case_*.csv

        if RUNNER == "docker":
            proc = _run_docker(in_dir, out_dir, control.name)
        else:
            proc = _run_local(control, out_dir)

        if proc.returncode != 0:
            raise RuntimeError(
                f"PSO run failed (runner={RUNNER}, exit {proc.returncode}).\n"
                f"--- stdout ---\n{proc.stdout[-2000:]}\n"
                f"--- stderr ---\n{proc.stderr[-2000:]}"
            )
        res = map_results(out_dir, snapshot=str(n.snapshots[0]))
        if not os.environ.get("DEVNET_PSO_KEEPTMP"):
            shutil.rmtree(tmp, ignore_errors=True)
        return res
    except Exception:
        # keep the temp case for debugging on failure
        print(f"[pso_engine] solve failed (runner={RUNNER}); case kept at {tmp}",
              file=sys.stderr)
        raise
