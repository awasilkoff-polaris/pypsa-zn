# Exercise the FULL DevNet repo pipeline (run_commit -> solve -> dashboards ->
# index.html) with the PSO engine on a real PSO 3.2 build. Drives the repo's own
# functions exactly as the interactive researcher_loop does on "C" (commit).
#
#   .venv\Scripts\python.exe pso\run_repo_32.py
#
# Uses pso.local.toml (engine=pso, pso_version=3.2, project=PSO-3.2-Main).

import sys, json
from types import SimpleNamespace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
import pypsa
import devnet_stress_lib as dsl

import os
print(f"engine={dsl.get_engine()}  version={os.environ.get('DEVNET_PSO_VERSION')}")
print(f"project={os.environ.get('DEVNET_PSO_PROJECT')}\n")

n0 = pypsa.Network()
n0.import_from_csv_folder(str(REPO / "devnet-reference-runs/devnet-sld-20Mar2026"))
OUT = REPO / "pso" / "cases" / "_repo32" / "stress_out"

GRAD = json.dumps({"WECC_NW": 10, "WECC_SW": 20, "SPP_MISO": 30,
                   "PJM_NE": 40, "SERC_SE": 50, "ERCOT": 60})


def mkargs(**kw):
    base = dict(scenario="single", solver="highs", outdir=str(OUT),
                k_load="{}", k_line="{}", mc_bus="{}", mc_mode="set",
                byog_mc=None, line="", kmin=1.0, kmax=0.2, kstep=-0.1,
                dc_p_set=None, dc_p_nom=None)
    base.update(kw)
    return SimpleNamespace(**base)


commits = [
    ("baseline",            mkargs(scenario="baseline")),
    ("mc_gradient",         mkargs(scenario="single", mc_bus=GRAD)),
    ("sweep SW_SPP 1.0->0.6", mkargs(scenario="sweep_line", mc_bus=GRAD,
                                     line="L_WECC_SW_SPP_MISO",
                                     kmin=1.0, kmax=0.6, kstep=-0.4)),  # 2 points
]

for label, args in commits:
    r = dsl.run_commit(n0, args, "devnet-sld")     # raises on error; prior commits persist
    print(f"[OK] {label:24s} commit={r['commit_id']} kind={r['kind']}")

print("\n--- artifacts in stress_out ---")
for p in sorted(OUT.glob("c*")):
    print("  ", p.name)
print("index.html:", (OUT / "index.html").exists(), OUT / "index.html")
