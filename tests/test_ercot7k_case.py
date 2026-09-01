#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 ZeroNode
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# test_ercot7k_case.py
#
# Purpose
#   Test ercot7k_case.py. Every negative test below corresponds to one SILENT
#   failure: a case that PSO reads without complaint, solves to optimality and
#   reports normally, while modelling something other than what was asked for.
#   Each asserts a raise, because a warning nobody reads is the failure mode.
#
# What it does
#   - Round-trips all 24 files of the real ercot7k/ case and asserts the bytes
#     are identical. Everything else stands on this.
#   - Builds two-layer chains on the mini7k fixture, tampers a parent, and
#     asserts the hash chain catches it.
#   - Builds a real datacenter layer off ercot7k/ and runs the full V1-V13.
#   - Asserts verify_case() passes clean on ercot7k/ itself. If the base fails
#     its own checks, the checks are wrong.
#
# Outputs
#   - pytest results only. Every layer is written under tmp_path.
#
# Run: python -m pytest tests/test_ercot7k_case.py -q
# ------------------------------------------------------------------------------

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ercot7k_case as ec  # noqa: E402

BASE_DIR = REPO_ROOT / "ercot7k"
MINI_DIR = REPO_ROOT / "tests" / "fixtures" / "mini7k"

# Nodes in the mini7k fixture. MONITORED_NODE is an endpoint of a Monitor=1
# branch; UNMONITORED_NODE is touched only by Monitor=0 branches, which is what
# V8 is looking for.
MONITORED_NODE = "N111179"
UNMONITORED_NODE = "N110001"

MINI_TIMEPOINTS = 12


# ------------------------------------------------------------------------------
#   Fixtures
# ------------------------------------------------------------------------------
@pytest.fixture
def mini_base(tmp_path: Path) -> Path:
    """A writable copy of mini7k, so a test may tamper with its parent."""
    target = tmp_path / "mini7k"
    shutil.copytree(MINI_DIR, target)
    return target


def mini_spec(node: str = MONITORED_NODE, **overrides) -> ec.DatacenterSpec:
    values = dict(dc_name="DC1", node=node, p_set_mw=100.0,
                  byog_p_nom_mw=50.0, byog_max_mw=50.0, byog_mc=65.0)
    values.update(overrides)
    return ec.DatacenterSpec(**values)


# ------------------------------------------------------------------------------
#   1. Byte-fidelity I/O. Nothing else in this module means anything if these
#      fail, so they come first.
# ------------------------------------------------------------------------------
def test_round_trip_of_the_real_case_is_byte_identical():
    files = ec.case_csv_files(BASE_DIR)
    assert len(files) == 24, "the ercot7k case is 24 CSV files"
    for path in files:
        table = ec.read_table(path)
        assert table.to_bytes() == path.read_bytes(), path.name


def test_round_trip_through_the_filesystem_is_byte_identical(tmp_path: Path):
    for path in ec.case_csv_files(BASE_DIR):
        target = tmp_path / path.name
        ec.write_table(ec.read_table(path), target)
        assert target.read_bytes() == path.read_bytes(), path.name


def test_field_text_is_never_reserialized():
    """746.000 must not become 746.0, on any of 634 rows."""
    table = ec.read_table(BASE_DIR / "texas7k_INJ_ID.csv")
    records = table.records()
    assert records[0]["MaxMw"] == "746.000"
    assert records[0]["MinMw"] == "0.000"
    assert records[0]["RaiseRR"] == "22.380"
    net = ec.read_table(BASE_DIR / "texas7k_INJ_NET.csv")
    assert net.records()[0]["LossFactor"] == "0.00000000"


def test_schema_is_introspected_from_the_file_not_hardcoded():
    table = ec.read_table(BASE_DIR / "texas7k_INJ_ID.csv")
    assert table.columns[0] == "Injector"
    assert table.columns.index("Area") == 2
    assert len(table.columns) == 15


def test_a_crlf_file_keeps_its_crlf():
    """CYC_SAI is the one CRLF file in the case; it must stay that way."""
    path = BASE_DIR / "texas7k_CYC_SAI.csv"
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert ec.read_table(path).to_bytes() == raw


def test_reading_a_non_ascii_file_is_refused(tmp_path: Path):
    path = tmp_path / "texas7k_X.csv"
    # The two bytes are UTF-8 for e-acute, written as an escape so this file
    # itself stays plain ASCII.
    path.write_bytes(b"A,B\n1,\xc3\xa9\n")
    with pytest.raises(ec.ByteFidelityError):
        ec.read_table(path)


def test_reading_a_bom_is_refused(tmp_path: Path):
    path = tmp_path / "texas7k_X.csv"
    path.write_bytes(b"\xef\xbb\xbfA,B\n1,2\n")
    with pytest.raises(ec.ByteFidelityError):
        ec.read_table(path)


# ------------------------------------------------------------------------------
#   2. Manifest, hashing and the chain walk
# ------------------------------------------------------------------------------
def test_a_two_layer_chain_walks_back_to_the_base(mini_base: Path,
                                                  tmp_path: Path):
    layer1 = tmp_path / ec.layer_dir_name(mini_base, "dc1")
    ec.build_datacenter_layer(mini_base, layer1, mini_spec())
    layer2 = tmp_path / ec.layer_dir_name(layer1, "dc2")
    ec.build_datacenter_layer(layer2.parent / layer1.name, layer2,
                              mini_spec(dc_name="DC2"))

    chain = ec.walk_chain(layer2)
    assert [c.kind for c in chain] == ["base", "layer", "layer"]
    assert chain[0].path == mini_base.resolve()
    assert chain[-1].path == layer2.resolve()


def test_a_hand_edited_parent_layer_is_caught_by_its_own_hash(mini_base: Path,
                                                             tmp_path: Path):
    layer1 = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer1, mini_spec())
    ec.walk_chain(layer1)  # clean before the edit

    target = layer1 / "texas7k_INJ_ID.csv"
    target.write_bytes(target.read_bytes().replace(b"100.000", b"900.000"))

    with pytest.raises(ec.ChainIntegrityError):
        ec.walk_chain(layer1)
    with pytest.raises(ec.ChainIntegrityError):
        ec.build_datacenter_layer(layer1, tmp_path / "layer2",
                                  mini_spec(dc_name="DC2"))


def test_a_hand_edited_base_is_caught_by_the_childs_parent_hash(
        mini_base: Path, tmp_path: Path):
    layer1 = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer1, mini_spec())

    target = mini_base / "texas7k_INJ_ID.csv"
    target.write_bytes(target.read_bytes().replace(b"746.000", b"747.000"))

    with pytest.raises(ec.ChainIntegrityError):
        ec.walk_chain(layer1)


def test_the_manifest_records_what_the_layer_owns(mini_base: Path,
                                                  tmp_path: Path):
    layer = tmp_path / "layer1"
    manifest = ec.build_datacenter_layer(mini_base, layer, mini_spec())

    assert manifest["schema"] == ec.MANIFEST_SCHEMA
    assert manifest["parent"]["kind"] == "base"
    assert manifest["parent"]["path"] == str(mini_base.resolve())
    assert manifest["self_sha256"] == ec.case_digest(layer)

    owned = {(e["table"], tuple(e["key"])) for e in manifest["owned"]}
    assert ("INJ_ID", ("DC1_LOAD",)) in owned
    assert ("INJ_ID", ("DC1_BYOG",)) in owned
    assert ("INJ_NET", ("DC1_LOAD",)) in owned
    for scenario in ("ScnSC", "ScnDA", "ScnRT"):
        assert ("SCN_INJ_DSP", (scenario, "DC1_LOAD")) in owned

    assert manifest["owned_files"] == ["texas7k_SCN_INJ_DSP.csv"]
    assert manifest["capacity_ceilings"]["DC1_BYOG"] == 50.0
    assert {o["table"] for o in manifest["deliberate_omissions"]} == {
        "INJ_CMT", "CYC_INJ_CCV", "RSV_INJ", "STE_NDE"}
    # The manifest is read back from disk as ASCII JSON, not just returned.
    on_disk = ec.read_manifest(layer)
    assert on_disk["layer_id"] == manifest["layer_id"]
    assert json.dumps(on_disk).isascii()


def test_a_manifest_is_written_once_and_never_edited(mini_base: Path,
                                                     tmp_path: Path):
    layer = tmp_path / "layer1"
    manifest = ec.build_datacenter_layer(mini_base, layer, mini_spec())
    with pytest.raises(ec.Ercot7kCaseError):
        ec.write_manifest(layer, manifest)


# ------------------------------------------------------------------------------
#   3. AddInjector, AddSchedule and the numbered SCH_TMP sibling
# ------------------------------------------------------------------------------
def test_a_datacenter_layer_leaves_every_untouched_file_byte_identical(
        mini_base: Path, tmp_path: Path):
    layer = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer, mini_spec())

    changed = {"texas7k_INJ_ID.csv", "texas7k_INJ_NET.csv"}
    new = {"texas7k_SCN_INJ_DSP.csv"}
    for path in ec.case_files(mini_base):
        target = layer / path.name
        if path.name in changed:
            assert target.read_bytes().startswith(path.read_bytes()), path.name
        else:
            assert target.read_bytes() == path.read_bytes(), path.name
    assert {p.name for p in ec.case_files(layer)} == (
        {p.name for p in ec.case_files(mini_base)} | new)


def test_the_new_injector_rows_carry_a_blank_area(mini_base: Path,
                                                  tmp_path: Path):
    layer = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer, mini_spec())
    records = ec.read_table(layer / "texas7k_INJ_ID.csv").records()
    added = {r["Injector"]: r for r in records
             if r["Injector"].startswith("DC1_")}
    assert set(added) == {"DC1_LOAD", "DC1_BYOG"}
    for record in added.values():
        assert record["Area"] == "", "Area must be blank, never 0"
    assert added["DC1_LOAD"]["LoadFlag"] == "1"
    assert added["DC1_LOAD"]["MaxMw"] == "100.000"
    assert added["DC1_BYOG"]["LoadFlag"] == "0"
    assert added["DC1_BYOG"]["EnergyCost"] == "65.000"


def test_add_schedule_writes_a_new_numbered_sch_tmp_sibling(mini_base: Path,
                                                            tmp_path: Path):
    layer = tmp_path / "layer1"
    values = tuple(float(i) for i in range(MINI_TIMEPOINTS))
    deltas = [ec.AddSchedule("DC1_shape", values, repeat_time=MINI_TIMEPOINTS)]
    manifest = ec.write_layer(mini_base, layer, deltas, slug="sched")

    sibling = layer / "texas7k_SCH_TMP2.csv"
    assert sibling.is_file()
    assert manifest["owned_files"] == ["texas7k_SCH_TMP2.csv"]
    # SCH_TMP1 is copied byte-identical and never parsed for content.
    assert (layer / "texas7k_SCH_TMP1.csv").read_bytes() == (
        (mini_base / "texas7k_SCH_TMP1.csv").read_bytes())
    # The sibling's header IS the original's header.
    assert sibling.read_bytes().split(b"\n")[0] == (
        (mini_base / "texas7k_SCH_TMP1.csv").read_bytes().split(b"\n")[0])

    rows = ec.read_table(sibling).records()
    assert len(rows) == MINI_TIMEPOINTS
    assert rows[0]["Time"] == "2018.04.06 00:00"
    assert rows[-1]["Time"] == "2018.04.06 11:00"
    assert all(r["Enforce"] == "1" for r in rows)
    assert not ec.has_errors(ec.verify_case(layer))


def test_a_third_layer_takes_sch_tmp3(mini_base: Path, tmp_path: Path):
    values = tuple(1.0 for _ in range(MINI_TIMEPOINTS))
    layer1 = tmp_path / "l1"
    ec.write_layer(mini_base, layer1,
                   [ec.AddSchedule("S1", values, repeat_time=MINI_TIMEPOINTS)],
                   slug="s1")
    layer2 = tmp_path / "l2"
    ec.write_layer(layer1, layer2,
                   [ec.AddSchedule("S2", values, repeat_time=MINI_TIMEPOINTS)],
                   slug="s2")
    assert (layer2 / "texas7k_SCH_TMP3.csv").is_file()
    assert (layer2 / "texas7k_SCH_TMP2.csv").read_bytes() == (
        (layer1 / "texas7k_SCH_TMP2.csv").read_bytes())


# ------------------------------------------------------------------------------
#   4. ScenarioOverride on SCN_INJ_DSP
# ------------------------------------------------------------------------------
def test_scn_inj_dsp_gets_one_row_per_named_scenario(mini_base: Path,
                                                     tmp_path: Path):
    layer = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer, mini_spec())
    table = ec.read_table(layer / "texas7k_SCN_INJ_DSP.csv")
    assert table.columns == list(ec.NEW_TABLE_COLUMNS["SCN_INJ_DSP"])
    rows = table.records()
    assert [r["Scenario"] for r in rows] == ["ScnSC", "ScnDA", "ScnRT"]
    for record in rows:
        assert record["Injector"] == "DC1_LOAD"
        assert record["Dispatch"] == "100.000"
        # Enforce is narrow: it means "Dispatch = 0 should be enforced". A
        # non-zero dispatch is enforced regardless, so it stays blank.
        assert record["Enforce"] == ""


def test_a_zero_mw_datacenter_sets_enforce(mini_base: Path, tmp_path: Path):
    tables = ec.read_case(mini_base)
    deltas = ec.datacenter_deltas(mini_spec(p_set_mw=0.0, byog_p_nom_mw=0.0,
                                            byog_max_mw=0.0), tables)
    overrides = [d for d in deltas if isinstance(d, ec.ScenarioOverride)]
    assert overrides and all(d.field_map()["Enforce"] == "1" for d in overrides)


def test_scenario_override_on_an_unsupported_table_names_the_registry(
        mini_base: Path, tmp_path: Path):
    for table_name in ("SCN_ARA_LOD", "SCN_INJ_MAX"):
        delta = ec.ScenarioOverride.of(table_name, "ScnRT", "0",
                                       {"ScaleFactor": "1.200"})
        with pytest.raises(NotImplementedError, match="SCN_TABLES"):
            ec.write_layer(mini_base, tmp_path / table_name, [delta])


def test_field_edit_is_absent_and_names_its_extension_point(mini_base: Path,
                                                            tmp_path: Path):
    delta = ec.FieldEdit("BRN_ID", ("N110001_N110041_1",), "Monitor", "0", "1")
    with pytest.raises(NotImplementedError, match="apply_deltas"):
        ec.write_layer(mini_base, tmp_path / "fe", [delta])


def test_stress_deltas_is_absent_and_names_its_extension_point():
    with pytest.raises(NotImplementedError, match="SCN_TABLES"):
        ec.stress_deltas([], {})


# ------------------------------------------------------------------------------
#   5. verify_case on the real base and on a real derived layer
# ------------------------------------------------------------------------------
def test_verify_case_passes_clean_on_the_base_itself():
    """If the base fails its own checks, the checks are wrong."""
    findings = ec.verify_case(BASE_DIR)
    assert not ec.has_errors(findings), ec.format_findings(findings)
    assert not [f for f in findings if f.level == ec.LEVEL_WARNING]


def test_verify_case_passes_clean_on_the_fixture():
    findings = ec.verify_case(MINI_DIR)
    assert not ec.has_errors(findings), ec.format_findings(findings)


def test_a_real_datacenter_layer_off_ercot7k_verifies_clean(tmp_path: Path):
    spec = ec.DatacenterSpec(dc_name="DC1", node="N110126", p_set_mw=1000.0,
                             byog_p_nom_mw=500.0, byog_max_mw=500.0,
                             byog_mc=65.0)
    layer = tmp_path / ec.layer_dir_name(BASE_DIR, "dc1")
    manifest = ec.build_datacenter_layer(BASE_DIR, layer, spec)

    findings = ec.verify_case(layer)
    assert not ec.has_errors(findings), ec.format_findings(findings)
    checks = {f.check for f in findings if f.level == ec.LEVEL_OK}
    for required in ("V1", "V2", "V3", "V4", "V5", "V7", "V8", "V9", "V10",
                     "V11", "V12", "V13"):
        assert required in checks, "%s did not execute: %s" % (
            required, ec.format_findings(findings))

    injectors = ec.read_table(layer / "texas7k_INJ_ID.csv").records()
    assert len(injectors) == 636
    assert manifest["study"]["datacenters"][0]["pin_mechanism"] == (
        "scn_inj_dsp_fixed")
    expected = manifest["study"]["datacenters"][0]["expected_dc_mw_by_interval"]
    assert len(expected) == 265
    assert expected["2018.04.06 00:00"] == 1000.0
    assert expected["2018.04.17 00:00"] == 1000.0


def test_the_datacenter_load_spans_the_whole_of_min_to_max_date():
    """MinDate..MaxDate, not StartDate..StopDate: SC carries 48 h of lead."""
    tables = ec.read_case(BASE_DIR)
    points = ec.model_timepoints(tables)
    mdl = ec.model_id(tables)
    assert points[0] == mdl["MinDate"]
    assert points[-1] == mdl["MaxDate"]
    assert len(points) == 265
    assert mdl["StartDate"] != mdl["MinDate"]
    assert mdl["StopDate"] != mdl["MaxDate"]


# ------------------------------------------------------------------------------
#   6. Negative tests. One per silent failure.
# ------------------------------------------------------------------------------
def test_area_zero_on_an_added_injector_is_refused(mini_base: Path,
                                                   tmp_path: Path,
                                                   monkeypatch):
    """Area=0 is a dummy injector: the case runs clean and does nothing."""
    original = ec.injector_id_values

    def with_area_zero(delta):
        values = original(delta)
        values["Area"] = "0"
        return values

    monkeypatch.setattr(ec, "injector_id_values", with_area_zero)
    layer = tmp_path / "layer1"
    with pytest.raises(ec.VerificationError, match="V2"):
        ec.build_datacenter_layer(mini_base, layer, mini_spec())
    assert not layer.exists(), "a rejected layer must not be emitted"


def test_a_duplicate_injector_name_is_refused(mini_base: Path, tmp_path: Path):
    """Duplicate primary keys coalesce non-blank fields, silently merging."""
    deltas = [
        ec.AddInjector("N111180_1", MONITORED_NODE, True, 100.0),
    ]
    with pytest.raises(ec.OwnershipError, match="COALESCE"):
        ec.write_layer(mini_base, tmp_path / "dup", deltas)


def test_a_layer_two_add_over_a_layer_one_key_is_refused(mini_base: Path,
                                                         tmp_path: Path):
    layer1 = tmp_path / "l1"
    ec.build_datacenter_layer(mini_base, layer1, mini_spec())
    with pytest.raises(ec.OwnershipError):
        ec.build_datacenter_layer(layer1, tmp_path / "l2", mini_spec())


def test_a_schedule_short_of_max_date_is_refused(mini_base: Path,
                                                 tmp_path: Path):
    """Values past the last time point read as ZERO, not hold-last."""
    short = tuple(1.0 for _ in range(MINI_TIMEPOINTS - 1))
    deltas = [ec.AddSchedule("DC1_shape", short,
                             repeat_time=MINI_TIMEPOINTS - 1)]
    with pytest.raises(ec.Ercot7kCaseError, match="MaxDate"):
        ec.write_layer(mini_base, tmp_path / "short", deltas)


def test_a_missing_scenario_row_is_refused(mini_base: Path, tmp_path: Path):
    """A missing scenario row is a silently switched-off datacenter."""
    deltas = [
        ec.AddInjector("DC1_LOAD", MONITORED_NODE, True, 100.0),
        ec.ScenarioOverride.of("SCN_INJ_DSP", "ScnSC", "DC1_LOAD",
                               {"Dispatch": "100.000"}),
        ec.ScenarioOverride.of("SCN_INJ_DSP", "ScnRT", "DC1_LOAD",
                               {"Dispatch": "100.000"}),
    ]
    with pytest.raises(ec.VerificationError, match="ScnDA"):
        ec.write_layer(mini_base, tmp_path / "missing", deltas)


def test_a_positive_min_mw_is_refused(mini_base: Path, tmp_path: Path):
    deltas = [ec.AddInjector("DC1_LOAD", MONITORED_NODE, True, 100.0,
                             min_mw=5.0)]
    with pytest.raises(ec.VerificationError, match="V7"):
        ec.write_layer(mini_base, tmp_path / "minmw", deltas)


def test_byog_p_nom_differing_from_byog_max_mw_is_refused(mini_base: Path,
                                                          tmp_path: Path):
    """Until SCN_INJ_MAX lands, INJ_ID.MaxMw alone sets BYOG capacity."""
    spec = mini_spec(byog_p_nom_mw=25.0, byog_max_mw=50.0)
    with pytest.raises(NotImplementedError, match="SCN_INJ_MAX"):
        ec.build_datacenter_layer(mini_base, tmp_path / "byog", spec)


def test_an_unknown_node_is_refused(mini_base: Path, tmp_path: Path):
    """An unmapped node falls back to area load distribution, silently."""
    spec = mini_spec(node="N999999")
    with pytest.raises(ec.Ercot7kCaseError, match="NDE_ID"):
        ec.build_datacenter_layer(mini_base, tmp_path / "node", spec)


def test_a_datacenter_at_an_unmonitored_node_warns_and_can_be_made_fatal(
        mini_base: Path, tmp_path: Path):
    """Only 1171 of 9140 branches are monitored; the rest report nothing."""
    layer = tmp_path / "warn"
    ec.build_datacenter_layer(mini_base, layer, mini_spec(node=UNMONITORED_NODE))
    findings = ec.verify_case(layer)
    warnings = [f for f in findings if f.check == "V8"]
    assert warnings and warnings[0].level == ec.LEVEL_WARNING

    with pytest.raises(ec.VerificationError, match="V8"):
        ec.build_datacenter_layer(mini_base, tmp_path / "strict",
                                  mini_spec(node=UNMONITORED_NODE),
                                  strict_monitored=True)


def test_a_tampered_derived_case_fails_the_v9_line_diff(mini_base: Path,
                                                        tmp_path: Path):
    layer = tmp_path / "layer1"
    ec.build_datacenter_layer(mini_base, layer, mini_spec())
    target = layer / "texas7k_NDE_ID.csv"
    target.write_bytes(target.read_bytes().replace(b"N110001", b"N110002", 1))
    findings = ec.verify_case(layer)
    v9 = [f for f in findings if f.check == "V9" and f.level == ec.LEVEL_ERROR]
    assert v9, ec.format_findings(findings)


def test_a_field_containing_a_comma_is_refused(mini_base: Path,
                                               tmp_path: Path):
    deltas = [ec.AddInjector("DC1_LOAD", MONITORED_NODE, True, 100.0,
                             name="Big, Datacenter")]
    with pytest.raises(ec.ByteFidelityError):
        ec.write_layer(mini_base, tmp_path / "comma", deltas)


def test_a_rejected_layer_leaves_nothing_behind(mini_base: Path,
                                                tmp_path: Path):
    out = tmp_path / "gone"
    with pytest.raises(ec.Ercot7kCaseError):
        ec.write_layer(mini_base, out,
                       [ec.AddInjector("N111180_1", MONITORED_NODE, True, 1.0)])
    assert not out.exists()
