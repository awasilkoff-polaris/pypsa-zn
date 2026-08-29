```text
SPDX-License-Identifier: Apache-2.0
Copyright 2026 ZeroNode
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
devnet_callstack.md
------------------------------------------------------------------------------
```

# DevNet Call Stack

Call trace of the DevNet network modelling, stress-analysis and reporting stack.

The workflow is split between:

- `devnet_stress.py` — interactive researcher shell and menu workflow.
- `lib/devnet_stress_lib.py` — non-interactive OPF execution, stress logic, result collection, dashboards and HTML reporting.

---

# Table of Contents
- [DevNet Call Stack](#devnet-call-stack)
- [Table of Contents](#table-of-contents)
  - [`devnet_stress.py` — interactive shell functions](#devnet_stresspy--interactive-shell-functions)
  - [`lib/devnet_stress_lib.py` — stress engine functions](#libdevnet_stress_libpy--stress-engine-functions)
    - [OPF / stress execution](#opf--stress-execution)
    - [Results / dashboards](#results--dashboards)
    - [Preview / commit / reporting](#preview--commit--reporting)
  - [devnet\_stress.py call stack map](#devnet_stresspy-call-stack-map)
    - [Entry branch: `main()`](#entry-branch-main)
    - [Interactive branch: `researcher_loop()` — R / C / Q loop](#interactive-branch-researcher_loop--r--c--q-loop)
    - [Commit branch: `researcher_loop()` when user selects `C`](#commit-branch-researcher_loop-when-user-selects-c)
    - [HTML/reporting branch: `dsl.update_index_html(...)`](#htmlreporting-branch-dslupdate_index_html)
    - [Lower-level compute primitives](#lower-level-compute-primitives)
  - [Deferred / Future Work](#deferred--future-work)
    - [`sweep_line` Workflow](#sweep_line-workflow)
- [Reference](#reference)

---

## `devnet_stress.py` — interactive shell functions

- `confirm(...)`
- `build_argparser(...)`
- `build_args_catalog(...)`
- `capture_catalog_lines(...)`
- `print_two_columns(...)`
- `prompt_custom_k_load(...)`
- `prompt_custom_k_line(...)`
- `prompt_custom_k_gen(...)`
- `prompt_custom_mc_bus(...)`
- `_pick_from_menu(...)`
- `configure_args_menu(...)`
- `print_dashboard(...)`
- `researcher_loop(...)`
- `main()`

## `lib/devnet_stress_lib.py` — stress engine functions

### OPF / stress execution

- `solve_with_duals(...)`
- `apply_load_multipliers(...)`
- `apply_corridor_reducers(...)`
- `apply_gen_capacity_multipliers(...)`
- `apply_gen_marginal_cost_by_bus(...)`
- `resolve_dc_csv_values(...)`
- `resolve_byog_mc(...)`
- `parse_json_dict(...)`
- `run_single(...)`
- `run_sweep_line(...)`

### Results / dashboards

- `collect_results(...)`
- `write_outputs(...)`
- `_dashboard_from_single(...)`
- `_dashboard_from_sweep(...)`
- `dashboard_text(...)`
- `devnet_base_params(...)`
- `build_sanity_panel_lines(...)`

### Preview / commit / reporting

- `run_preview(...)`
- `_next_commit_id(...)`
- `run_commit(...)`
- `write_commit_dashboard_md(...)`
- `update_index_html(...)`

---

## devnet_stress.py call stack map

### Entry branch: `main()`

main()
├─ build_argparser(DEVNET_BLD_PATH)
│  └─ argparse.parse_args()
├─ build_args_catalog(devnet)
│  └─ dsl.resolve_dc_csv_values(devnet)
├─ capture_catalog_lines(catalog)
├─ dsl.build_sanity_panel_lines(devnet)
│  └─ dsl.devnet_base_params(devnet)
├─ print_two_columns(left, right, ...)
├─ dsl.update_index_html(
│      args.outdir,
│      devnet,
│      DEVNET_NAME
│  )
└─ researcher_loop(devnet, args, catalog)

---

### Interactive branch: `researcher_loop()` — R / C / Q loop

researcher_loop(devnet, args, catalog)
│
├─ configure_args_menu(devnet, args)
│  ├─ build_args_catalog(devnet)
│  │  └─ dsl.resolve_dc_csv_values(devnet)
│  ├─ _pick_from_menu(...)
│  ├─ prompt_custom_k_load(...)       # optional
│  ├─ prompt_custom_k_line(...)       # optional
│  ├─ prompt_custom_k_gen(...)        # optional
│  ├─ prompt_custom_mc_bus(...)       # optional
│  └─ configures:
│     ├─ scenario
│     ├─ mc_mode
│     ├─ k_load
│     ├─ k_line
│     ├─ k_gen
│     ├─ mc_bus
│     ├─ lmp_bus
│     ├─ byog_mc
│     ├─ dc_p_set
│     ├─ dc_p_nom
│     ├─ line
│     ├─ kmin
│     ├─ kmax
│     └─ kstep
│
├─ dsl.run_preview(devnet, args, DEVNET_NAME)
│
│  ├─ baseline / single
│  │   └─ dsl.run_single(...)
│  │       ├─ apply_load_multipliers(...)
│  │       ├─ apply_corridor_reducers(...)
│  │       ├─ apply_gen_capacity_multipliers(...)
│  │       ├─ apply_gen_marginal_cost_by_bus(...)
│  │       ├─ optional DC load/BYOG overrides
│  │       ├─ optional `Gen_DC_PJM_NE`
│  │       ├─ solve_with_duals(...)
│  │       ├─ collect_results(...)
│  │       └─ write_outputs(...)
│  │
│  └─ sweep_line
│      └─ dsl.run_sweep_line(...)
│
├─ dsl._dashboard_from_single(
│      res,
│      report_bus=args.lmp_bus
│  )
│      OR
│  dsl._dashboard_from_sweep(df)
│
└─ print_dashboard(...)
   └─ dsl.dashboard_text(...)
   
---

### Commit branch: `researcher_loop()` when user selects `C`

researcher_loop(...) [cmd == "c"]
│
├─ dsl.run_commit(
│      devnet,
│      args,
│      DEVNET_NAME
│  )
│
│  ├─ _next_commit_id(args.outdir)
│  │
│  ├─ run_single(...)
│  │      OR
│  │  run_sweep_line(...)
│  │
│  ├─ _dashboard_from_single(
│  │      res,
│  │      report_bus=args.lmp_bus
│  │  )
│  │      OR
│  │  _dashboard_from_sweep(df)
│  │
│  ├─ dashboard_text(...)
│  ├─ write_commit_dashboard_md(...)
│  └─ update_index_html(
│         args.outdir,
│         devnet,
│         DEVNET_NAME
│     )
│
├─ prints committed dashboard / Commit ID / index.html path
│
└─ refreshes researcher display:
   ├─ build_args_catalog(devnet)
   ├─ capture_catalog_lines(catalog)
   ├─ dsl.build_sanity_panel_lines(devnet)
   └─ print_two_columns(...)

---

### HTML/reporting branch: `dsl.update_index_html(...)`

update_index_html(
    outdir,
    devnet,
    devnet_name,
    use_http_paths=False
)
│
├─ scans stress_out/ for:
│  ├─ cN_dashboard.md
│  └─ cN_*.csv
│
├─ reads commit summary metrics
│
├─ loads:
│  └─ lib/devnet_stress_frame.html
│
└─ generates stress_out/index.html
   ├─ Commit Summary table
   ├─ per-commit dashboard cards
   ├─ links to generated CSV artifacts
   ├─ DevNet SLD image
   └─ sanity / base-parameter panel
   
---

### Lower-level compute primitives

solve_with_duals(n, solver)
└─ n.optimize(
     solver_name=solver,
     assign_all_duals=True
   )

apply_load_multipliers(n, k_load)
└─ applies regional load stress
   └─ explicit DC loads are excluded

apply_corridor_reducers(n, k_line)
└─ scales line s_nom

apply_gen_capacity_multipliers(n, k_gen)
└─ scales generation p_nom by bus
   ├─ 1.0 = unchanged
   ├─ 0.5 = 50% derating
   └─ 0.0 = outage

apply_gen_marginal_cost_by_bus(n, mc_bus, mode)
└─ changes generation marginal cost by bus

run_single(...)
├─ deepcopy(base network)
├─ apply stress parameters
├─ apply optional DC p_set / BYOG p_nom / BYOG MC
├─ add `Gen_DC_PJM_NE` for devnetDC-sld
├─ solve OPF
├─ collect:
│  ├─ objective
│  ├─ total system load
│  ├─ generator dispatch
│  ├─ bus import/export
│  ├─ LMP
│  └─ line loading
└─ write result artifacts

collect_results(...)
└─ bus_net_import_mw sign convention:
   ├─ positive = IMPORT
   └─ negative = EXPORT
   
---

## Deferred / Future Work

### `sweep_line` Workflow

`sweep_line` is currently unused by the validated Datacenter BYOG experiments. A sanity check identified that the sweep variable requires future review before this path is treated as a supported research workflow. See local TODO for the point fix and menu/argument-display cleanup.

---

# Reference
- chatGPT: Zeronode.ca > PyPSA overview::  
  [PyPSA Ramp & Dev](https://chatgpt.com/g/g-p-6857abd95a648191886783a41ba46a15-zeronode-ca/c/68d4302d-a6ec-8333-a0de-f3cfba0f2f26)

- chatGPT: Zeronode.ca > PyPSA Ramp & Dev2::  
  [PyPSA Ramp & Dev2](https://chatgpt.com/g/g-p-6857abd95a648191886783a41ba46a15-zeronode-ca/c/69680406-a7c8-8328-95d8-08889046f1b2)

- chatGPT: Zeronode.ca > PyPSA Ramp & Dev3::  
  [PyPSA Ramp & Dev3](https://chatgpt.com/g/g-p-6857abd95a648191886783a41ba46a15/c/6972f266-e858-832e-b4d8-ec7ed137bbfc)  

- chatGPT: Zeronode.ca > PyPSA Ramp & Dev4::  
  [PyPSA Ramp & Dev4](https://chatgpt.com/g/g-p-6857abd95a648191886783a41ba46a15/c/697a6437-af9c-8320-aa69-6b42cc0cb940)  

- chatGPT: Zeronode.ca > PyPSA Ramp & Dev5::  
  [PyPSA Ramp & Dev5](https://chatgpt.com/g/g-p-6857abd95a648191886783a41ba46a15/c/69d2fe05-9dc8-83e8-9ee5-76de3843ca5c)  

---

*Prepared collaboratively with ChatGPT-5, April 2026*