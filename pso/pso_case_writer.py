#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# pso_case_writer.py -- shared writer for PSO input cases (3.2 or 3.3 format).
#
# Single source of truth for the PSO CSV format. Used by both:
#   - pypsa_to_pso.py     (standalone scenario-driven converter)
#   - lib/pso_engine.py   (in-repo engine swap: live PyPSA network -> PSO)
#
# Format is version-toggled (pso_version, default "3.2"):
#   "3.2": header rows prefixed with '//', MDL_ID MinorRelease=2 (3.2 / 3.2+ compat
#          mode). Runs on real PSO 3.2 AND on 3.3 (3.3 reads '//' headers via the
#          alt-name DEX mapping and runs MinorRelease=2 in 3.2-compat mode). The
#          portable default.
#   "3.3": no '//' prefix, MinorRelease=3 (native 3.3).
# In both, VectorReports=1 in the control file -> long-format "vector" reports
# (results_PC_Nd / PN_Pth / MC_Solution ...), the shape pso_to_results.py reads.
#
# Other format facts: branches Enforce=1 + Resistance per caller (0 = lossless to
# match PyPSA DC-OPF), nodes ReportNode=1, single 1-interval / one-period / one-solve.

from pathlib import Path


def _mw(x):   return f"{float(x):.3f}"
def _imp(x):  return f"{float(x):.8f}"
def _cost(x): return f"{float(x):.3f}"


def _write(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")


def write_pso_case(out_dir, prefix, *, buses, branches, injectors,
                   node_loads, total_load, slack_bus, horizon,
                   pso_version="3.2") -> Path:
    """
    buses       : iterable of bus (Enode) names
    branches    : list of {name, fr, to, x, r, limit}
    injectors   : list of {name, node, maxmw, cost, loadflag}
    node_loads  : {bus: load_mw}   (buses absent default to 0)
    total_load  : float            (sum of node loads; SCN_ARA_LOD.Load)
    slack_bus   : str
    horizon     : {start: "YYYY.MM.DD HH:MM", interval_hours: int}
    pso_version : "3.2" (default, portable) or "3.3"
    Returns the control-file path (the SelectedDataFile PSO opens).
    """
    ver = str(pso_version).strip()
    is32 = ver.startswith("3.2")
    hp = "//" if is32 else ""        # header prefix
    minor = "2" if is32 else "3"     # MDL_ID MinorRelease

    out_dir = Path(out_dir)
    p = out_dir / prefix  # stem: <prefix>.csv (control) + <prefix>_<TABLE>.csv

    def w(path, header, rows):
        _write(path, hp + header, rows)

    w(Path(f"{p}_NDE_ID.csv"),
      "Enode,Name,Busbar,Substation,Area,ReportNode",
      [f"{b},,,,,1" for b in buses])

    w(Path(f"{p}_BRN_ID.csv"),
      "Branch,Name,FrEnode,ToEnode,Circuit,Voltage,Resistance,Reactance,"
      "NormalLimit,CtgLimit,Enforce,Monitor,Penalty,AngleLimit,HVDC,CID,PriceCID",
      [f"{b['name']},,{b['fr']},{b['to']},,0.000,{_imp(b['r'])},{_imp(b['x'])},"
       f"{_mw(b['limit'])},0.000,1,1,0.000,0.000,0,,0" for b in branches])

    w(Path(f"{p}_INJ_ID.csv"),
      "Injector,Name,Area,LoadFlag,Link,MaxMw,MinMw,RaiseRR,LowerRR,MinTime,"
      "RampSuSd,EnergyCost,CostAdder,RampUpCost,RampDnCost",
      [f"{g['name']},,,{g.get('loadflag',0)},0,{_mw(g['maxmw'])},0.000,0.000,"
       f"0.000,0.000,0,{_cost(g['cost'])},0.000,0.000,0.000" for g in injectors])

    w(Path(f"{p}_INJ_NET.csv"),
      "Injector,Node,PhysicalArea,LossFactor,IgnoreLoss",
      [f"{g['name']},{g['node']},,{_imp(0.0)},0" for g in injectors])

    w(Path(f"{p}_STE_NDE.csv"),
      "State,Enode,GenMw,LoadMw,GenNode,LoadNode",
      [f"0,{b},0.000,{_mw(node_loads.get(b, 0.0))},0,0" for b in buses])

    w(Path(f"{p}_SCN_ARA_LOD.csv"),
      "Scenario,Area,Load,Enforce,ScaleFactor,Schedule,Sequence",
      [f"0,0,{_mw(total_load)},,,,"])

    start = horizon["start"]
    ymd, hm = start.split(" ")
    stop_h = int(hm.split(":")[0]) + int(horizon.get("interval_hours", 1))
    w(Path(f"{p}_MDL_ID.csv"),
      "Name,MajorRelease,MinorRelease,BranchRelease,TimeUnit,IntervalLength,"
      "MinDate,MaxDate,StartDate,StopDate",
      [f",3,{minor},0,hour,1,2012.01.01 00:00,2012.01.06 00:00,{start},{ymd} {stop_h:02d}:00"])

    w(Path(f"{p}_CYC_ID.csv"),
      "Cycle,Name,DeltaTime,LeadTime,DecisionTime,OrderTime,MipGap,"
      "MipGapAbsolute,MaxSolveTime,MaxIterations",
      ["DA,,1,0,0,0,0.00000000,0.000,0.000,0"])
    w(Path(f"{p}_CYC_PRD_ID.csv"), "Cycle,Period,Length", ["DA,1,1"])
    w(Path(f"{p}_CYC_SCN.csv"), "Cycle,Scenario,Weight,Reference",
      ["DA,ScnDA,1.00000000,0"])

    w(Path(f"{p}.csv"),
      "OptionName,OptionValue",
      ["DateFormat,%c%y.%m.%d %H:%M", "MipGap,0.0001",
       f"SlackBusName,{slack_bus}", "NumberOfInputTabs,1", "VectorReports,1"])

    return Path(f"{p}.csv")
