# PyPSA side of the stress parity check: load the reference 6-bus network, apply
# the per-bus marginal-cost perturbation, solve, and print objective / per-bus LMP
# / per-line flow — to compare against the PSO devnet_stress_mc run.
#
#   .venv\Scripts\python.exe pso\parity_pypsa.py

import pypsa

# Per-bus generator marginal cost (matches devnet_stress_mc/devnet_INJ_ID.csv)
MC_BY_BUS = {
    "WECC_NW": 10.0, "WECC_SW": 20.0, "SPP_MISO": 30.0,
    "PJM_NE": 40.0, "SERC_SE": 50.0, "ERCOT": 60.0,
}

n = pypsa.Network()
n.import_from_csv_folder("devnet-reference-runs/devnet-sld-20Mar2026")

# Apply the marginal-cost stress (generators are named Gen_<bus>)
for gen in n.generators.index:
    bus = n.generators.at[gen, "bus"]
    n.generators.at[gen, "marginal_cost"] = MC_BY_BUS[bus]

n.optimize(solver_name="highs", assign_all_duals=True)

print("objective: %.3f" % n.objective)

print("\nper-bus LMP:")
lmp = n.buses_t.marginal_price.iloc[0]
for bus in n.buses.index:
    print(f"  {bus:10s} {lmp[bus]:8.3f}")

print("\nper-line flow (MW, bus0->bus1) and loading:")
p0 = n.lines_t.p0.iloc[0]
for ln in n.lines.index:
    load = p0[ln] / n.lines.at[ln, "s_nom"]
    print(f"  {ln:22s} {p0[ln]:10.3f}  ({load:+.3f} pu)")

print("\nper-gen dispatch (MW):")
p = n.generators_t.p.iloc[0]
for gen in n.generators.index:
    print(f"  {gen:14s} {p[gen]:10.3f}")
