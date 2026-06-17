# Run a PSO case via aimmspy and report key results.
#
# This is the Python (aimmspy) backend -- the lead deployment path: the client
# runs PSO with THEIR OWN AIMMS install + license (e.g. an academic license via
# license_url), against the (encrypted) PSO model. No Docker required.
#
# All machine/site-specific settings come from env vars (no hardcoded paths), so
# the same script runs on a dev box or a client machine:
#
#   DEVNET_PSO_PROJECT       full path to PSO.aimms                 (required)
#   DEVNET_PSO_AIMMS_PATH    explicit AIMMS /Bin path               (optional)
#   DEVNET_PSO_AIMMS_VERSION AIMMS version for find_aimms_path()    (optional, e.g. "26.1")
#                            -- ignored if DEVNET_PSO_AIMMS_PATH is set;
#                               if neither set, uses the most recent install
#   DEVNET_PSO_LICENSE_URL   academic/cloud license URL (wss://...) (optional)
#                            -- omit to use the machine's configured AIMMS license
#
# Usage: run_pso.py <INPUT_CONTROL_CSV> <RESULTS_CSV>
#   python run_pso.py "D:/.../case.csv" "D:/.../out/results.csv"

import os
import sys
from pathlib import Path

# Apply pso.local.toml as env defaults (no-op if run as a subprocess that already
# inherited them from the parent). Must precede reading DEVNET_PSO_* below.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import pso_config
    pso_config.load_config()
except Exception:
    pass

from aimmspy.project.project import Project
from aimmspy.utils import find_aimms_path

os.environ["PSO_AIMMS_MODE"] = "Python"

# ---- config (all from env / args -- no hardcoded paths) ----
PROJECT = os.environ.get("DEVNET_PSO_PROJECT")               # path to PSO.aimms (required)
AIMMS_PATH = os.environ.get("DEVNET_PSO_AIMMS_PATH")          # explicit /Bin, optional
AIMMS_VERSION = os.environ.get("DEVNET_PSO_AIMMS_VERSION")    # e.g. "26.1", optional
LICENSE_URL = os.environ.get("DEVNET_PSO_LICENSE_URL")        # academic/cloud license, optional

if len(sys.argv) < 3:
    sys.exit("usage: run_pso.py <INPUT_CONTROL_CSV> <RESULTS_CSV>")
INPUT, RESULTS = sys.argv[1], sys.argv[2]

if not PROJECT:
    sys.exit("DEVNET_PSO_PROJECT not set (path to PSO.aimms) -- set it in pso.local.toml or the env")

Path(RESULTS).parent.mkdir(parents=True, exist_ok=True)
assert Path(INPUT).is_file(), f"INPUT not found: {INPUT}"
assert Path(PROJECT).is_file(), f"PSO project not found: {PROJECT} (set DEVNET_PSO_PROJECT)"

# Resolve the AIMMS install: explicit path > pinned version > match aimmspy.
if AIMMS_PATH:
    aimms_path = AIMMS_PATH
elif AIMMS_VERSION:
    aimms_path = find_aimms_path(AIMMS_VERSION)
else:
    # Default: match the AIMMS runtime to the installed aimmspy major.minor
    # (e.g. aimmspy 26.1.x -> AIMMS 26.1), not blindly the newest install.
    try:
        import aimmspy
        _mm = ".".join(str(aimmspy.__version__).split(".")[:2])
        aimms_path = find_aimms_path(_mm)
    except Exception:
        aimms_path = find_aimms_path()

# license_url is academic-only; omit the kwarg entirely otherwise (don't pass None).
project_kwargs = {"aimms_path": aimms_path, "aimms_project_file": PROJECT}
if LICENSE_URL:
    project_kwargs["license_url"] = LICENSE_URL

print(f">> opening AIMMS project\n   aimms_path={aimms_path}\n   project={PROJECT}"
      f"\n   license_url={'(set)' if LICENSE_URL else '(machine default)'}", flush=True)
project = Project(**project_kwargs)
aimms_model = project.get_model(__file__)

aimms_model.SelectedDataFile.assign(INPUT)
aimms_model.ResultsFile.assign(RESULTS)
aimms_model.FileOptionString.assign("CSV text")

print(">> StartupDataID()", flush=True)
try:
    aimms_model.StartupDataID()
    print(">> StartupDataID returned OK", flush=True)
except Exception as e:
    print(">> StartupDataID error:", e, flush=True)
    raise

# List what landed in the results dir so we can locate the LMP / objective reports.
outdir = Path(RESULTS).parent
files = sorted(p.name for p in outdir.glob("*"))
print(f">> {len(files)} files written to {outdir}:", flush=True)
for f in files:
    print("   ", f, flush=True)
print(">> done", flush=True)
