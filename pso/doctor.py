#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# doctor.py -- preflight check for the PSO engine. Catches the common (and silent)
# setup problems before you run a case, with an actionable hint for each.
#
# Run from the MAIN env (.venv):
#   .venv\Scripts\python.exe pso\doctor.py
#
# It checks PyPSA in this env and probes the separate solve interpreter
# (.venv-pso, via DEVNET_PSO_PYTHON) for aimmspy + a matching AIMMS install.
# Exit 0 if ready to solve; non-zero if a critical check fails.

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pso_config

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[warn]"
crit_fail = False

# probe run in the solve interpreter: report aimmspy version + resolved AIMMS path
_PROBE = r'''
import json, os
out = {}
try:
    import aimmspy
    from aimmspy.utils import find_aimms_path
    out["aimmspy"] = str(aimmspy.__version__)
    ap = os.environ.get("DEVNET_PSO_AIMMS_PATH")
    av = os.environ.get("DEVNET_PSO_AIMMS_VERSION")
    if ap:
        out["aimms"] = ap
    elif av:
        out["aimms"] = str(find_aimms_path(av))
    else:
        out["aimms"] = str(find_aimms_path(".".join(out["aimmspy"].split(".")[:2])))
except Exception as e:
    out["error"] = repr(e)
print(json.dumps(out))
'''


def line(status, label, detail="", hint=""):
    global crit_fail
    if status == BAD:
        crit_fail = True
    print(f"  {status} {label}" + (f": {detail}" if detail else ""))
    if hint:
        print(f"         -> {hint}")


def main():
    print("PSO engine doctor")
    print("=" * 60)

    applied = pso_config.load_config()
    cfg = pso_config.CONFIG_FILE
    if cfg.is_file():
        line(OK, "config file", str(cfg), f"applied {len(applied)} setting(s)")
    else:
        line(WARN, "config file", "not found",
             f"copy pso.local.toml.example -> {cfg.name} (or use env vars)")

    engine = os.environ.get("DEVNET_ENGINE", "pypsa").lower()
    runner = os.environ.get("DEVNET_PSO_RUNNER", "local").lower()
    line(OK, "engine", engine, "" if engine == "pso" else "set engine='pso' to solve with PSO")

    # PyPSA lives in THIS (main) env -- builds/transforms the network.
    try:
        import pypsa
        line(OK, "pypsa (main env)", pypsa.__version__)
    except Exception as e:
        line(BAD, "pypsa (main env)", str(e), "uv sync  (or python -m venv + pip)")

    if engine == "pso":
        _check_docker() if runner == "docker" else _check_local()

    print("=" * 60)
    if crit_fail:
        print("NOT READY -- fix the [FAIL] item(s) above.")
        sys.exit(1)
    print("READY.")
    sys.exit(0)


def _check_local():
    solve_py = os.environ.get("DEVNET_PSO_PYTHON", sys.executable)
    if not Path(solve_py).exists():
        line(BAD, "solve interpreter", solve_py,
             "set python='.../.venv-pso/Scripts/python.exe' in pso.local.toml (run setup.ps1)")
        return
    line(OK, "solve interpreter", solve_py)

    # Probe aimmspy + AIMMS in the solve interpreter (not this one).
    r = subprocess.run([solve_py, "-c", _PROBE], capture_output=True, text=True)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        line(BAD, "aimmspy", (r.stderr.strip().splitlines() or ["probe failed"])[-1],
             "the solve env lacks aimmspy: uv pip install --python <.venv-pso> aimmspy")
        return
    if "error" in out:
        line(BAD, "aimmspy / AIMMS", out["error"],
             "install aimmspy in .venv-pso matching your AIMMS, or set aimms_path/aimms_version")
        return

    line(OK, "aimmspy (solve env)", out.get("aimmspy", "?"))
    aimms = out.get("aimms")
    if aimms and Path(aimms).exists():
        line(OK, "AIMMS install", aimms)
    else:
        line(BAD, "AIMMS install", f"resolved but missing: {aimms}",
             "install a matching AIMMS, or set aimms_path/aimms_version in pso.local.toml")

    if os.environ.get("DEVNET_PSO_LICENSE_URL"):
        line(OK, "license", "license_url set (academic/cloud)")
    else:
        line(WARN, "license", "using machine-configured AIMMS license",
             "set license_url in pso.local.toml if you use an academic/cloud license")

    proj = os.environ.get("DEVNET_PSO_PROJECT")
    if not proj:
        line(BAD, "PSO model", "DEVNET_PSO_PROJECT not set",
             "set project='...path/PSO.aimms' in pso.local.toml")
    elif not Path(proj).is_file():
        line(BAD, "PSO model", f"not found: {proj}", "check the project path")
    else:
        line(OK, "PSO model", proj)


def _check_docker():
    if shutil.which(os.environ.get("DEVNET_PSO_DOCKER", "docker")):
        line(OK, "docker", "found on PATH")
    else:
        line(BAD, "docker", "not found", "install Docker Desktop / add docker to PATH")
    img = os.environ.get("DEVNET_PSO_IMAGE", "pso-pso33:3.3")
    line(WARN, "docker image", img,
         "ensure this image is pulled/loaded; set DEVNET_PSO_DOCKER_ARGS for licensing")


if __name__ == "__main__":
    main()
