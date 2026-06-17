#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# pypsa_to_pso.py -- standalone PyPSA -> PSO input converter.
#
# Reads a base network in PyPSA CSV format (buses/lines/generators/loads, as
# exported by devnet_sld.py) plus a lightweight scenario spec (scenarios.yaml),
# and writes one PSO 3.3 input case per scenario into <out_dir>/<name>/.
#
# Each scenario applies the repo's own stress levers (same meaning as
# devnet_stress.py's --k_load / --k_line / --mc_bus / --mc_mode):
#   k_load  {bus: multiplier}   scale load p_set
#   k_line  {line: factor}      derate line s_nom (factor in (0,1])
#   mc_bus  {bus: cost}         generator marginal cost (mc_mode: set | add)
#   byog    {bus, p_set, mc, [p_nom]}   add a datacenter load + BYOG generator
#
# The PSO file format itself lives in pso_case_writer.write_pso_case (shared with
# the in-repo engine swap, lib/pso_engine.py).
#
# Usage (run in the project's uv venv -- needs pandas + pyyaml):
#   .venv\Scripts\python.exe pso\pypsa_to_pso.py [scenarios.yaml]

import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pso_case_writer import write_pso_case

REPO = Path(__file__).resolve().parent.parent  # repo root


def load_base(network_dir: Path) -> dict:
    def read(name, idx="name"):
        return pd.read_csv(network_dir / name).set_index(idx)
    return {
        "buses": read("buses.csv"),
        "lines": read("lines.csv"),
        "generators": read("generators.csv"),
        "loads": read("loads.csv"),
    }


def build_case(base: dict, scn: dict, cfg: dict, out_root: Path) -> Path:
    """Apply a scenario's perturbations and emit one PSO case."""
    name = scn["name"]
    k_load = scn.get("k_load", {}) or {}
    k_line = scn.get("k_line", {}) or {}
    mc_bus = scn.get("mc_bus", {}) or {}
    mc_mode = scn.get("mc_mode", "set")
    byog = scn.get("byog")

    buses = list(base["buses"].index)
    lines, gens, loads = base["lines"], base["generators"], base["loads"]

    branches = [
        {"name": ln, "fr": r["bus0"], "to": r["bus1"], "x": float(r["x"]),
         "r": 0.0, "limit": float(r["s_nom"]) * float(k_line.get(ln, 1.0))}
        for ln, r in lines.iterrows()
    ]

    def gen_cost(bus, base_cost):
        if bus in mc_bus:
            return float(mc_bus[bus]) if mc_mode == "set" else base_cost + float(mc_bus[bus])
        return base_cost

    injectors = [
        {"name": g, "node": r["bus"], "maxmw": float(r["p_nom"]),
         "cost": gen_cost(r["bus"], float(r["marginal_cost"])), "loadflag": 0}
        for g, r in gens.iterrows()
    ]

    load_by_bus = loads.groupby("bus")["p_set"].sum()
    node_loads = {b: float(load_by_bus.get(b, 0.0)) * float(k_load.get(b, 1.0)) for b in buses}

    if byog:
        bus = byog["bus"]
        node_loads[bus] = node_loads.get(bus, 0.0) + float(byog.get("p_set", 0.0))
        injectors.append({
            "name": f"Gen_BYOG_{bus}", "node": bus,
            "maxmw": float(byog.get("p_nom", byog.get("p_set", 0.0))),
            "cost": float(byog.get("mc", 0.0)), "loadflag": 0,
        })

    return write_pso_case(
        out_root / name, name,
        buses=buses, branches=branches, injectors=injectors,
        node_loads=node_loads, total_load=sum(node_loads.values()),
        slack_bus=cfg["slack_bus"], horizon=cfg["horizon"],
        pso_version=str(cfg.get("pso_version", "3.2")),
    )


def main():
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "pso" / "scenarios.yaml"
    cfg = yaml.safe_load(spec_path.read_text())

    base_dir = (REPO / cfg["base_network"]).resolve()
    out_root = (REPO / cfg["out_dir"]).resolve()
    base = load_base(base_dir)

    print(f"base network: {base_dir}")
    print(f"  {len(base['buses'])} buses, {len(base['lines'])} lines, "
          f"{len(base['generators'])} gens, {len(base['loads'])} loads")
    print(f"output root:  {out_root}\n")

    for scn in cfg["scenarios"]:
        ctrl = build_case(base, scn, cfg, out_root)
        print(f"  [{scn['name']:16s}] -> {ctrl}")


if __name__ == "__main__":
    main()
