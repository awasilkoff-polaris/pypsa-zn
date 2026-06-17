# Smoke test: drive the repo's own run_single() with the PSO engine swapped in.
# Proves DEVNET_ENGINE=pso routes the existing stress path through PSO unchanged.
#   .venv\Scripts\python.exe pso\test_engine_swap.py
import os
os.environ["DEVNET_ENGINE"] = "pso"

import sys, json
from types import SimpleNamespace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import pypsa
import devnet_stress_lib as dsl

n = pypsa.Network()
n.import_from_csv_folder(str(REPO / "devnet-reference-runs/devnet-sld-20Mar2026"))

outdir = REPO / "pso" / "cases" / "_integration" / "stress_out"
args = SimpleNamespace(
    k_load="{}", k_line="{}",
    mc_bus=json.dumps({"WECC_NW": 10, "WECC_SW": 20, "SPP_MISO": 30,
                       "PJM_NE": 40, "SERC_SE": 50, "ERCOT": 60}),
    mc_mode="set", solver="highs",
    outdir=str(outdir), byog_mc=None, dc_p_set=None, dc_p_nom=None,
)

res = dsl.run_single(n, args, "mc_gradient", "devnet-sld")
print("status   :", res.get("status"))
print("objective:", res["objective"])
print("lmp      :", {k: round(v, 1) for k, v in res["lmp"].items()})
print("loading  :", {k: round(v, 3) for k, v in res["line_loading_pu"].items()})
print("artifacts:", sorted(p.name for p in outdir.glob("mc_gradient*")))
