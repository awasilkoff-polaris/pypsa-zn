================================================================================
ASR-DASH::COMMIT::c12::baseline
----------------------------------------
args: scenario=baseline  mc_mode=set  line=-  k_load={}  k_line={"L_PJM_NE_SERC_SE": 0.1}  k_gen={}  mc_bus={}  lmp_bus=PJM_NE  byog_mc=CSV Preset::50.0  dc_p_set=CSV Preset::2000.0  dc_p_nom=3000.0
objective        : 1.587e+06
total_load_mw     : 32,000.0
generator_dispatch_mw:
  Gen_WECC_NW             :    5,000.0
  Gen_WECC_SW             :    5,000.0
  Gen_SPP_MISO            :    5,000.0
  Gen_PJM_NE              :    3,333.3
  Gen_SERC_SE             :    5,666.7
  Gen_ERCOT               :    5,000.0
  Gen_DC_PJM_NE           :    3,000.0
bus_import_export_mw (+IMPORT / -EXPORT):
  WECC_NW                 :        0.0
  WECC_SW                 :        0.0
  SPP_MISO                :        0.0
  PJM_NE                  :      666.7
  SERC_SE                 :     -666.7
  ERCOT                   :        0.0
PJM_NE_lmp        : 60.000
lmp_spread       : 5.000   max_lmp: 60.000 @ PJM_NE
max_loading_pu   : 1.000 @ L_PJM_NE_SERC_SE
near_bind_ct(>=.95): 1
top_lines        : L_PJM_NE_SERC_SE:1.00 | L_SPP_MISO_ERCOT:0.03 | L_SPP_MISO_PJM_NE:0.03
================================================================================
