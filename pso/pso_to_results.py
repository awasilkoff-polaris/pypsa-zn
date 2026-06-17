#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# pso_to_results.py -- PSO results adapter.
#
# Maps a PSO run's output CSVs into the exact artifact shapes the DevNet repo
# produces (lib/devnet_stress_lib.py: collect_results + write_outputs), so the
# stress dashboards / plots / index.html can consume PSO results unchanged:
#
#   <tag>_lmp.csv             pd.Series(name="lmp")          bus  -> LMP
#   <tag>_line_loading_pu.csv pd.Series(name="loading_pu")   line -> |flow|/limit
#   <tag>_objective.csv       pd.Series({"objective": ...})
#   <tag>.json                {snapshot, objective, dc_dispatch_mw, lmp, line_loading_pu}
#
# PSO source files (in the run's results dir):
#   results_PC_Nd.csv        nodal LMP   (nd, LMP)  -- drop Reference_* rows
#   results_PN_Pth.csv       branch flow (pth, Mw, Max)  -> loading = |Mw| / Max
#   results_MC_Solution.csv  objective + solver Status
#   results_ED_Inj.csv       injector dispatch (for BYOG datacenter MW)
#
# Note: the repo's loading_pu uses abs(flow)/s_nom, so we use |Mw|/Max (Max is the
# branch's signed limit = the post-derate NormalLimit). Matches the repo exactly.
#
# Usage (uv venv -- pandas + numpy):
#   .venv\Scripts\python.exe pso\pso_to_results.py <pso_results_dir> <out_dir> <tag>

import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _read(path: Path) -> pd.DataFrame:
    """Read a PSO result CSV, stripping the leading '//' from the header."""
    df = pd.read_csv(path)
    df.columns = [c.lstrip("/") for c in df.columns]
    return df


def map_results(results_dir: Path, snapshot: str = "now") -> dict:
    results_dir = Path(results_dir)

    # --- objective + solver status (single solve row) ---
    sol = _read(results_dir / "results_MC_Solution.csv").iloc[0]
    objective = float(sol["Objective"])
    status = str(sol["Status"])

    # --- nodal LMP (drop reference pseudo-nodes) ---
    nd = _read(results_dir / "results_PC_Nd.csv")
    nd = nd[~nd["nd"].astype(str).str.startswith("Reference_")]
    lmp = {row["nd"]: float(row["LMP"]) for _, row in nd.iterrows()}

    # --- line loading pu = |Mw| / limit (abs, matching the repo) ---
    pth = _read(results_dir / "results_PN_Pth.csv")
    loading = {}
    for _, row in pth.iterrows():
        limit = float(row["Max"])
        loading[row["pth"]] = abs(float(row["Mw"])) / limit if limit else np.nan

    # --- datacenter BYOG dispatch, if a BYOG injector is present ---
    dc_dispatch_mw = np.nan
    inj_path = results_dir / "results_ED_Inj.csv"
    if inj_path.exists():
        inj = _read(inj_path)
        byog = inj[inj["inj"].astype(str).str.startswith(("Gen_BYOG_", "Gen_DC_"))]
        if not byog.empty:
            dc_dispatch_mw = float(byog["P"].astype(float).sum())

    return {
        "snapshot": snapshot,
        "objective": objective,
        "status": status,
        "dc_dispatch_mw": dc_dispatch_mw,
        "lmp": lmp,
        "line_loading_pu": loading,
    }


def write_outputs(out_dir: Path, tag: str, results: dict) -> None:
    """Mirror lib/devnet_stress_lib.write_outputs so artifacts are drop-in."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / f"{tag}.json").open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    if results.get("lmp"):
        pd.Series(results["lmp"], name="lmp").to_csv(out_dir / f"{tag}_lmp.csv")
    if results.get("line_loading_pu"):
        pd.Series(results["line_loading_pu"], name="loading_pu").to_csv(
            out_dir / f"{tag}_line_loading_pu.csv")
    pd.Series({"objective": results.get("objective", np.nan)}).to_csv(
        out_dir / f"{tag}_objective.csv")


def main():
    if len(sys.argv) < 4:
        print("usage: pso_to_results.py <pso_results_dir> <out_dir> <tag>")
        sys.exit(2)
    results_dir, out_dir, tag = sys.argv[1], sys.argv[2], sys.argv[3]

    res = map_results(results_dir)
    write_outputs(out_dir, tag, res)

    print(f"status:    {res['status']}")
    print(f"objective: {res['objective']:.3f}")
    print("lmp:")
    for b, v in res["lmp"].items():
        print(f"  {b:10s} {v:8.3f}")
    print("line_loading_pu:")
    for ln, v in res["line_loading_pu"].items():
        print(f"  {ln:22s} {v:+.4f}")
    print(f"\nwrote {tag}_lmp.csv / {tag}_line_loading_pu.csv / "
          f"{tag}_objective.csv / {tag}.json -> {out_dir}")


if __name__ == "__main__":
    main()
