#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# pso_config.py -- load pso.local.toml into the DEVNET_PSO_* environment.
#
# One small config file at the repo root (pso.local.toml, gitignored) instead of
# remembering env vars. Values are applied as env defaults: an already-set env var
# always wins, so CI / one-off overrides still work. Both the in-repo engine
# (lib/pso_engine via devnet_stress_lib) and run_pso.py call load_config(), so the
# settings reach the solve no matter how it's launched.
#
# Copy pso.local.toml.example -> pso.local.toml and edit. Example:
#   engine      = "pso"
#   project     = "D:/path/to/PSO.aimms"
#   license_url = "wss://licensing.aimms.cloud/..."   # academic/cloud; omit for machine license

import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # 3.10 backport, if installed
    except ModuleNotFoundError:
        tomllib = None

REPO = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO / "pso.local.toml"

# toml key -> environment variable
_MAP = {
    "engine":        "DEVNET_ENGINE",
    "runner":        "DEVNET_PSO_RUNNER",
    "pso_version":   "DEVNET_PSO_VERSION",
    "project":       "DEVNET_PSO_PROJECT",
    "license_url":   "DEVNET_PSO_LICENSE_URL",
    "aimms_path":    "DEVNET_PSO_AIMMS_PATH",
    "aimms_version": "DEVNET_PSO_AIMMS_VERSION",
    "python":        "DEVNET_PSO_PYTHON",
    "slack":         "DEVNET_PSO_SLACK",
    "image":         "DEVNET_PSO_IMAGE",
    "docker_args":   "DEVNET_PSO_DOCKER_ARGS",
}


def load_config(path: Path = CONFIG_FILE) -> dict:
    """Apply pso.local.toml as env defaults (existing env vars take precedence)."""
    if tomllib is None or not Path(path).is_file():
        return {}
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    applied = {}
    for key, env in _MAP.items():
        if key in cfg and cfg[key] not in (None, "") and env not in os.environ:
            os.environ[env] = str(cfg[key])
            applied[env] = os.environ[env]
    return applied


if __name__ == "__main__":
    applied = load_config()
    if applied:
        print(f"loaded {CONFIG_FILE}:")
        for k, v in applied.items():
            print(f"  {k} = {v}")
    else:
        print(f"no config applied (missing {CONFIG_FILE} or all keys already in env)")
