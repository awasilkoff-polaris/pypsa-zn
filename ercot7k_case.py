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

# ercot7k_case.py
#
# Purpose
#   Derive a new PSO case directory from an immutable parent case (ercot7k/)
#   by appending a declared set of rows -- a datacenter load injector, its
#   behind-the-meter generator (BYOG), their fixed-dispatch scenario rows and
#   any new schedules -- without disturbing a single byte of anything else.
#
#   This is the FIRST importable-only module at the repo root. Every other root
#   script (devnetDC_sld.py, devnet_stress.py, ercot7k_pso.py) executes on
#   import: it prints banners, prompts for a run name, creates directories and
#   replaces sys.stdout. Importing one of those from a test would hang. This
#   module therefore does NOTHING at import time -- no prints, no prompts, no
#   directory creation, no logging setup, no sys.stdout replacement. All of the
#   house-style operator behaviour lives in the front end (ercot7k_build.py,
#   Milestone 2), which is the only thing ever run from the menu. Read the
#   quietness here as deliberate, not as an oversight.
#
# What it does
#   - Reads a PSO case directory with byte fidelity. Schema is introspected
#     from each file's own header line, never a hardcoded column list, and
#     every untouched line is carried through verbatim (ASCII, per-line
#     terminator, no BOM, trailing newline, column order, "746.000" stays
#     "746.000").
#   - Writes a derived layer: unchanged files copied byte-identically, changed
#     files appended to, new files created (texas7k_SCN_INJ_DSP.csv and a new
#     numbered sibling texas7k_SCH_TMP<N>.csv).
#   - Writes one pso_case_manifest.json per layer recording what it owns, and
#     walks the parent chain back to the base, re-hashing every layer so a
#     hand-edited generated case is a hard error.
#   - Verifies a case directory against checks V1-V13 with no PSO run. This
#     runs standalone on ANY case directory, including ercot7k/ itself: if the
#     base fails its own checks, the checks are wrong.
#
# Outputs
#   - <out_dir>/ a complete PSO case directory (all parent files + new tables)
#   - <out_dir>/pso_case_manifest.json
#
# Run: python ercot7k_case.py verify ercot7k
# ------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Global defines
SECTION_SEPARATOR = "="*80 + "\n"  # for print separation
SUBSECTION_SEPARATOR = "-"*40 + "\n"  # for print separation

WRITER_ID = "ercot7k_case.py/1"
MANIFEST_NAME = "pso_case_manifest.json"
MANIFEST_SCHEMA = "ercot7k-case-manifest/1"

DEFAULT_PREFIX = "texas7k"

# The control file (texas7k.csv) carries no table suffix. It is held in the
# table map under this synthetic name so that one dict covers the directory.
CONTROL_TABLE = "OPTIONS"

# Primary keys, taken from the "primary-key" front matter of the PSO input
# documentation pages under documentation_wiki/wiki/inputs/. A table absent
# from this map is copied byte-identically and is not key-checked (V3 reports
# that as a SKIP rather than silently passing).
#
# INJ_STG_CMT is documented as keyed on (Injector) alone but the shipped file
# carries a Cycle column; the base uses a single cycle (DA) so both readings
# agree. The narrower documented key is used, which is the stricter check.
TABLE_KEYS: Dict[str, Tuple[str, ...]] = {
    "OPTIONS": ("OptionName",),
    "BRN_ID": ("Branch",),
    "CCV_ATT": ("CostCurve",),
    "CCV_PNT": ("CostCurve", "Point"),
    "CYC_ARA_CMT": ("Cycle", "Area"),
    "CYC_ID": ("Cycle",),
    "CYC_INJ_CCV": ("Cycle", "Injector"),
    "CYC_PRD_ID": ("Cycle", "Period"),
    "CYC_SAI": ("Cycle",),
    "CYC_SCN": ("Cycle", "Scenario"),
    "INJ_CMT": ("Injector",),
    "INJ_ID": ("Injector",),
    "INJ_NET": ("Injector",),
    "INJ_STG_CMT": ("Injector",),
    "NDE_ID": ("Enode",),
    "RSV_ARA": ("ReserveType", "Area"),
    "RSV_ID": ("ReserveType",),
    "RSV_INJ": ("ReserveType", "Injector"),
    "SCH_ATT": ("Schedule",),
    "SCH_TMP": ("Schedule", "Time"),
    "SCN_ARA_LOD": ("Scenario", "Area"),
    "SCN_INJ_DSP": ("Scenario", "Injector"),
    "SCN_INJ_MAX": ("Scenario", "Injector"),
    "STE_NDE": ("State", "Enode"),
}

# Scenario-override registry. Adding a new SCN_* table costs one entry here,
# one referential rule in verify_case() and one row in the merge table of the
# design document. `supported` is what this milestone implements; the rest are
# absent by design and raise NotImplementedError naming this registry.
SCN_TABLES: Dict[str, Dict[str, Any]] = {
    "SCN_INJ_DSP": {
        "key_fields": ("Scenario", "Injector"),
        "supported": True,
        "milestone": 1,
    },
    "SCN_ARA_LOD": {
        "key_fields": ("Scenario", "Area"),
        "supported": False,
        "milestone": 2,
        "note": (
            "k_load. Requires read-modify-rewrite of EVERY row and refusal of a "
            "literal scenario -- a ScaleFactor written only to row '0' leaves "
            "ScnRT, the reported cycle, at 1.0."
        ),
    },
    "SCN_INJ_MAX": {
        "key_fields": ("Scenario", "Injector"),
        "supported": False,
        "milestone": 2,
        "note": (
            "BYOG capacity sweeps. Requires the scheduled-renewable rule: the "
            "167 injectors that already carry availability schedules may set "
            "ScaleFactor only, never MaxMw."
        ),
    },
}

# The only headers in this module that are AUTHORED rather than introspected
# from an existing file, because these tables do not exist in the base case.
# Field order and spelling are taken from the PSO input documentation page for
# the table. Everything else in the writer reads its schema off the file.
NEW_TABLE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "SCN_INJ_DSP": (
        "Scenario", "Injector", "Dispatch", "Enforce",
        "ScaleFactor", "Schedule", "Sequence",
    ),
}

# Written into the manifest so that a later reader does not "fix" them.
DELIBERATE_OMISSIONS: Tuple[Dict[str, str], ...] = (
    {"table": "INJ_CMT",
     "reason": "no commitment on the datacenter load or on BYOG"},
    {"table": "CYC_INJ_CCV",
     "reason": "scalar cost only, so byog_mc means what it says and the "
               "cost-additivity trap cannot reach it"},
    {"table": "RSV_INJ",
     "reason": "the datacenter and BYOG are not reserve providers"},
    {"table": "STE_NDE",
     "reason": "an explicit INJ_NET.Node fully specifies location; STE_NDE is "
               "the area-load distribution mechanism and its LoadMw is a "
               "weight, not MW"},
)

# Named scenarios that a fixed-dispatch injector must carry a row for. Read
# from CYC_SCN at run time; this is only the fallback used in error text.
EXPECTED_SCENARIOS: Tuple[str, ...] = ("ScnSC", "ScnDA", "ScnRT")

# Field formats. The base writes MW-like quantities at 3 decimal places and
# "fine" quantities (LossFactor) at 8. Matching that keeps a derived layer
# visually indistinguishable from the case it came from.
FMT_MW = "%.3f"
FMT_FINE = "%.8f"

# MDL_ID dates in this case read as "2018.04.06 00:00". The control file gives
# DateFormat as "%c%y.%m.%d %H:%M", where %c%y is the AIMMS century-plus-year
# pair; Python spells that pair "%Y".
DATE_FORMAT_PY = "%Y.%m.%d %H:%M"
_AIMMS_DATE_TRANSLATIONS = (("%c%y", "%Y"),)

TIME_UNIT_SECONDS: Dict[str, int] = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}

LEVEL_OK = "OK"
LEVEL_SKIP = "SKIP"
LEVEL_WARNING = "WARNING"
LEVEL_ERROR = "ERROR"


# ------------------------------------------------------------------------------
#   Exceptions
# ------------------------------------------------------------------------------
class Ercot7kCaseError(Exception):
    """Base class for every error this module raises deliberately."""


class ByteFidelityError(Ercot7kCaseError):
    """A file could not be read or written without changing its bytes."""


class OwnershipError(Ercot7kCaseError):
    """A delta collides with a key already present or owned in the chain."""


class ChainIntegrityError(Ercot7kCaseError):
    """A layer in the parent chain no longer hashes to its recorded digest."""


class VerificationError(Ercot7kCaseError):
    """verify_case() returned an ERROR finding, so the layer is not emitted."""


# ------------------------------------------------------------------------------
# _split_lines_keepends()
#
# Splits text on LF only, keeping the terminator on each line.
#
# str.splitlines() is not usable here: it also breaks on VT, FF, FS, GS, RS and
# NEL, none of which are line breaks to a CSV reader. A file containing one of
# those inside a field would silently gain a line and lose byte fidelity.
# ------------------------------------------------------------------------------
def _split_lines_keepends(text: str) -> List[str]:
    lines: List[str] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            lines.append(text[start:index + 1])
            start = index + 1
    if start < len(text):
        lines.append(text[start:])
    return lines


# ------------------------------------------------------------------------------
# _terminator_of()
#
# Returns the exact line terminator carried by a raw line: "\r\n", "\n", or ""
# for a final line with no trailing newline.
# ------------------------------------------------------------------------------
def _terminator_of(raw_line: str) -> str:
    if raw_line.endswith("\r\n"):
        return "\r\n"
    if raw_line.endswith("\n"):
        return "\n"
    return ""


# ------------------------------------------------------------------------------
# _parse_line()
#
# Parses one raw CSV line into its field texts, verbatim. No type coercion of
# any kind happens anywhere in this module: a field is text until a check asks
# it to be a number, and it is written back as the text it came in as.
# ------------------------------------------------------------------------------
def _parse_line(raw_line: str) -> List[str]:
    body = raw_line
    for terminator in ("\r\n", "\n"):
        if body.endswith(terminator):
            body = body[:-len(terminator)]
            break
    return next(csv.reader([body]))


# ------------------------------------------------------------------------------
# _serialize_fields()
#
# Joins field texts into a CSV line. Deliberately refuses to quote: a field
# needing quoting in a case this writer generated would mean a name or a
# number arrived with a comma, quote or newline in it, which is a caller bug
# and not something to paper over with an escaping rule the base never uses.
# ------------------------------------------------------------------------------
def _serialize_fields(fields: Sequence[str], terminator: str) -> str:
    for value in fields:
        if any(bad in value for bad in (",", '"', "\r", "\n")):
            raise ByteFidelityError(
                "field text %r contains a comma, quote or newline; the base "
                "case quotes nothing, so this writer refuses to introduce "
                "quoting" % (value,)
            )
        if not value.isascii():
            raise ByteFidelityError(
                "field text %r is not ASCII; the base case is plain ASCII "
                "throughout" % (value,)
            )
    return ",".join(fields) + terminator


# ------------------------------------------------------------------------------
#   Table -- one CSV file, held as its own bytes
# ------------------------------------------------------------------------------
# raw_lines holds every line of the file including the header, each with its
# own terminator still attached. to_text() is a join, so a file that is read
# and written unchanged is byte-identical by construction rather than by a
# serializer that happens to agree with the source. columns is introspected
# from raw_lines[0] and is never assumed.
# ------------------------------------------------------------------------------
@dataclass
class Table:
    name: str
    filename: str
    raw_lines: List[str]

    @property
    def columns(self) -> List[str]:
        return _parse_line(self.raw_lines[0])

    @property
    def terminator(self) -> str:
        return _terminator_of(self.raw_lines[0])

    @property
    def data_lines(self) -> List[str]:
        return self.raw_lines[1:]

    def rows(self) -> List[List[str]]:
        return [_parse_line(line) for line in self.data_lines]

    def records(self) -> List[Dict[str, str]]:
        columns = self.columns
        out: List[Dict[str, str]] = []
        for index, line in enumerate(self.data_lines, start=2):
            fields = _parse_line(line)
            if len(fields) != len(columns):
                raise ByteFidelityError(
                    "%s line %d has %d fields, header has %d"
                    % (self.filename, index, len(fields), len(columns))
                )
            out.append(dict(zip(columns, fields)))
        return out

    def build_line(self, values: Dict[str, str]) -> str:
        columns = self.columns
        unknown = sorted(set(values) - set(columns))
        if unknown:
            raise Ercot7kCaseError(
                "%s has no column(s) %s; header is %s"
                % (self.filename, ", ".join(unknown), ",".join(columns))
            )
        fields = [values.get(column, "") for column in columns]
        return _serialize_fields(fields, self.terminator)

    def append_record(self, values: Dict[str, str]) -> str:
        if self.data_lines and _terminator_of(self.raw_lines[-1]) == "":
            raise ByteFidelityError(
                "%s does not end with a newline; appending would rewrite its "
                "last line and break the V9 line-level diff" % self.filename
            )
        line = self.build_line(values)
        self.raw_lines.append(line)
        return line

    def to_text(self) -> str:
        return "".join(self.raw_lines)

    def to_bytes(self) -> bytes:
        return self.to_text().encode("ascii")


# ------------------------------------------------------------------------------
# read_table()
#
# Reads one CSV file into a Table with byte fidelity. Rejects a BOM and any
# non-ASCII byte outright rather than decoding them into something that will
# not survive the round trip.
# ------------------------------------------------------------------------------
def read_table(path: Path, name: Optional[str] = None) -> Table:
    path = Path(path)
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ByteFidelityError("%s starts with a UTF-8 BOM" % path)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ByteFidelityError("%s is not plain ASCII: %s" % (path, exc))
    if not text:
        raise ByteFidelityError("%s is empty" % path)
    raw_lines = _split_lines_keepends(text)
    for index, line in enumerate(raw_lines[1:], start=2):
        if line.strip() == "":
            raise ByteFidelityError("%s line %d is blank" % (path, index))
    if name is None:
        name = table_name_for(path.name)
    return Table(name=name, filename=path.name, raw_lines=raw_lines)


# ------------------------------------------------------------------------------
# write_table()
#
# Writes a Table back out. Binary mode, so no platform newline translation can
# turn LF into CRLF behind our back.
# ------------------------------------------------------------------------------
def write_table(table: Table, path: Path) -> None:
    Path(path).write_bytes(table.to_bytes())


# ------------------------------------------------------------------------------
# table_name_for() / case_prefix() / case_files()
#
# Filename conventions of a PSO case directory: one control file <prefix>.csv
# and tables <prefix>_<TABLE>.csv. The prefix is discovered from the directory
# rather than assumed, so a renamed case still reads (V10 is what objects to a
# rename, and it should object with a finding, not with a crash here).
# ------------------------------------------------------------------------------
def table_name_for(filename: str, prefix: str = DEFAULT_PREFIX) -> str:
    stem = filename[:-4] if filename.lower().endswith(".csv") else filename
    if stem == prefix:
        return CONTROL_TABLE
    if stem.startswith(prefix + "_"):
        return stem[len(prefix) + 1:]
    return stem


def case_prefix(case_dir: Path) -> str:
    case_dir = Path(case_dir)
    candidates = sorted(
        p.stem for p in case_dir.glob("*.csv")
        if "_" not in p.stem
    )
    if not candidates:
        raise Ercot7kCaseError(
            "%s has no control file <prefix>.csv" % case_dir
        )
    if len(candidates) > 1:
        raise Ercot7kCaseError(
            "%s has more than one candidate control file: %s"
            % (case_dir, ", ".join(candidates))
        )
    return candidates[0]


def case_files(case_dir: Path) -> List[Path]:
    """Every regular file of a case directory, manifest excluded, sorted."""
    case_dir = Path(case_dir)
    return sorted(
        (p for p in case_dir.iterdir()
         if p.is_file() and p.name != MANIFEST_NAME),
        key=lambda p: p.name,
    )


def case_csv_files(case_dir: Path) -> List[Path]:
    return [p for p in case_files(case_dir) if p.suffix.lower() == ".csv"]


# ------------------------------------------------------------------------------
# read_case()
#
# Reads every CSV of a case directory into a name -> Table map. Non-CSV files
# (the README) are not parsed; they are still copied verbatim by write_layer()
# and still counted by V10.
# ------------------------------------------------------------------------------
def read_case(case_dir: Path, prefix: Optional[str] = None) -> Dict[str, Table]:
    case_dir = Path(case_dir)
    if prefix is None:
        prefix = case_prefix(case_dir)
    tables: Dict[str, Table] = {}
    for path in case_csv_files(case_dir):
        name = table_name_for(path.name, prefix)
        tables[name] = read_table(path, name=name)
    return tables


# ------------------------------------------------------------------------------
# sch_tmp_tables() / next_sch_tmp_index()
#
# SCH_TMP is spread over numbered files SCH_TMP1, SCH_TMP2, ... PSO globs
# <prefix>_SCH_TMP*.csv, so a new layer takes the next free index rather than
# rewriting the 3.5 MB SCH_TMP1 it inherited. The primary key (Schedule, Time)
# is unique across the whole set, not per file -- see V3.
# ------------------------------------------------------------------------------
def sch_tmp_tables(tables: Dict[str, Table]) -> List[Tuple[int, Table]]:
    found: List[Tuple[int, Table]] = []
    for name, table in tables.items():
        if name.startswith("SCH_TMP") and name[len("SCH_TMP"):].isdigit():
            found.append((int(name[len("SCH_TMP"):]), table))
    return sorted(found, key=lambda pair: pair[0])


def next_sch_tmp_index(tables: Dict[str, Table]) -> int:
    existing = sch_tmp_tables(tables)
    return 1 + max((n for n, _ in existing), default=0)


# ------------------------------------------------------------------------------
#   Hashing and the manifest chain
# ------------------------------------------------------------------------------
# self_sha256 is the digest of a layer's CASE CONTENT, not of its manifest: a
# sorted "<filename>:<sha256>" listing of every file except the manifest,
# hashed. That is what makes "re-hash every layer against its own self_sha256"
# detect a hand-edited generated case, which is the whole point of the chain.
# ------------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_digests(case_dir: Path) -> Dict[str, str]:
    return {path.name: sha256_file(path) for path in case_files(case_dir)}


def case_digest(case_dir: Path) -> str:
    digests = file_digests(case_dir)
    listing = "".join(
        "%s:%s\n" % (name, digests[name]) for name in sorted(digests)
    )
    return hashlib.sha256(listing.encode("ascii")).hexdigest()


def manifest_path(case_dir: Path) -> Path:
    return Path(case_dir) / MANIFEST_NAME


def has_manifest(case_dir: Path) -> bool:
    return manifest_path(case_dir).is_file()


def read_manifest(case_dir: Path) -> Dict[str, Any]:
    path = manifest_path(case_dir)
    if not path.is_file():
        raise Ercot7kCaseError("%s has no %s" % (case_dir, MANIFEST_NAME))
    with open(path, "r", encoding="ascii") as handle:
        manifest = json.load(handle)
    schema = manifest.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise Ercot7kCaseError(
            "%s has schema %r, expected %r" % (path, schema, MANIFEST_SCHEMA)
        )
    return manifest


def write_manifest(case_dir: Path, manifest: Dict[str, Any]) -> Path:
    path = manifest_path(case_dir)
    if path.exists():
        raise Ercot7kCaseError(
            "%s already exists; a manifest is written once and never edited"
            % path
        )
    text = json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=True)
    path.write_bytes((text + "\n").encode("ascii"))
    return path


# ------------------------------------------------------------------------------
# walk_chain()
#
# Follows parent.path back to the base layer, re-hashing every layer against
# the digest it recorded for itself. A mismatch means someone hand-edited a
# generated case, or edited the immutable base, and is a hard error: every
# ownership decision downstream is computed from content that is no longer the
# content that was signed for.
#
# Returns oldest-first, so chain[0] is always the base.
# ------------------------------------------------------------------------------
@dataclass
class ChainLayer:
    kind: str                      # "base" or "layer"
    path: Path
    layer_id: Optional[str]
    manifest: Optional[Dict[str, Any]]


def walk_chain(case_dir: Path) -> List[ChainLayer]:
    case_dir = Path(case_dir).resolve()
    chain: List[ChainLayer] = []
    seen: set = set()
    current = case_dir
    while True:
        if current in seen:
            raise ChainIntegrityError(
                "parent chain loops at %s" % current
            )
        seen.add(current)
        if not current.is_dir():
            raise ChainIntegrityError("chain parent %s does not exist" % current)
        if not has_manifest(current):
            chain.append(ChainLayer("base", current, None, None))
            break
        manifest = read_manifest(current)
        recorded = manifest.get("self_sha256")
        actual = case_digest(current)
        if recorded != actual:
            raise ChainIntegrityError(
                "%s no longer matches its manifest self_sha256 (recorded %s, "
                "actual %s); a generated case was hand-edited"
                % (current, recorded, actual)
            )
        chain.append(
            ChainLayer("layer", current, manifest.get("layer_id"), manifest)
        )
        parent = manifest.get("parent") or {}
        parent_path = parent.get("path")
        if not parent_path:
            raise ChainIntegrityError(
                "%s manifest has no parent.path" % current
            )
        parent_dir = Path(parent_path).resolve()
        parent_recorded = parent.get("self_sha256")
        parent_actual = case_digest(parent_dir) if parent_dir.is_dir() else None
        if parent_recorded and parent_actual != parent_recorded:
            raise ChainIntegrityError(
                "parent %s no longer matches the self_sha256 recorded by %s "
                "(recorded %s, actual %s); the parent case was hand-edited"
                % (parent_dir, current, parent_recorded, parent_actual)
            )
        current = parent_dir
    chain.reverse()
    return chain


# ------------------------------------------------------------------------------
# chain_owned_keys() / chain_capacity_ceilings()
#
# Union of everything any layer in the chain has declared it owns. Adds that
# collide with an owned key are errors, never overwrites. The physical-presence
# check in _check_add_collision() would catch most of these on its own; the
# owned map exists so the error can name the layer that owns the key.
# ------------------------------------------------------------------------------
def chain_owned_keys(chain: Sequence[ChainLayer]) -> Dict[Tuple[str, Tuple[str, ...]], str]:
    owned: Dict[Tuple[str, Tuple[str, ...]], str] = {}
    for layer in chain:
        if not layer.manifest:
            continue
        layer_id = layer.manifest.get("layer_id") or str(layer.path)
        for entry in layer.manifest.get("owned", []):
            key = (entry["table"], tuple(entry["key"]))
            owned[key] = layer_id
    return owned


def chain_capacity_ceilings(chain: Sequence[ChainLayer]) -> Dict[str, float]:
    ceilings: Dict[str, float] = {}
    for layer in chain:
        if not layer.manifest:
            continue
        for injector, ceiling in (layer.manifest.get("capacity_ceilings") or {}).items():
            ceilings[injector] = float(ceiling)
    return ceilings


# ------------------------------------------------------------------------------
#   Delta model
# ------------------------------------------------------------------------------
# Four frozen types. A delta list is an ordered list[Delta]; order matters only
# for readability, because collisions are errors rather than overwrites.
#
# AddInjector deliberately has NO area parameter. All 634 base injectors carry
# a blank Area, and an Area of "0" makes the injector a "dummy" that sits
# outside power balance -- the case runs clean and the datacenter does nothing.
# Not exposing the field is the cheapest way to make that mistake unavailable;
# V2 is the backstop.
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Delta:
    """Marker base class for the delta types."""


@dataclass(frozen=True)
class AddInjector(Delta):
    injector: str
    node: str
    load_flag: bool
    max_mw: float
    min_mw: float = 0.0
    energy_cost: float = 0.0
    name: str = ""


@dataclass(frozen=True)
class AddSchedule(Delta):
    schedule: str
    values: Tuple[float, ...]
    repeat_time: int = 265
    step_change: bool = True


@dataclass(frozen=True)
class ScenarioOverride(Delta):
    table: str
    scenario: str
    key: str
    fields: Tuple[Tuple[str, str], ...]

    @staticmethod
    def of(table: str, scenario: str, key: str,
           fields: Dict[str, str]) -> "ScenarioOverride":
        return ScenarioOverride(
            table=table, scenario=scenario, key=key,
            fields=tuple(sorted(fields.items())),
        )

    def field_map(self) -> Dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True)
class FieldEdit(Delta):
    table: str
    key: Tuple[str, ...]
    field: str
    old: str
    new: str


# ------------------------------------------------------------------------------
#   Study specification
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class DatacenterSpec:
    dc_name: str
    node: str
    p_set_mw: float
    byog_p_nom_mw: float
    byog_max_mw: float
    byog_mc: float
    load_shape: str = "flat"

    @property
    def load_injector(self) -> str:
        return "%s_LOAD" % self.dc_name

    @property
    def byog_injector(self) -> str:
        return "%s_BYOG" % self.dc_name


# ------------------------------------------------------------------------------
#   MDL_ID horizon arithmetic
# ------------------------------------------------------------------------------
# New schedules must span MinDate..MaxDate inclusive, NOT StartDate..StopDate.
# A time-point schedule is not extrapolated: values past its last point read as
# ZERO, not hold-last. SC carries 48 h of lead time and DA looks 24 h ahead, so
# a schedule that stops at StopDate switches the datacenter off inside the lead
# window of the very cycles that are supposed to plan for it.
# ------------------------------------------------------------------------------
def aimms_date_format(tables: Dict[str, Table]) -> str:
    options = tables.get(CONTROL_TABLE)
    if options is None:
        return DATE_FORMAT_PY
    for record in options.records():
        if record.get("OptionName") == "DateFormat":
            fmt = record.get("OptionValue", "")
            for aimms, python in _AIMMS_DATE_TRANSLATIONS:
                fmt = fmt.replace(aimms, python)
            return fmt or DATE_FORMAT_PY
    return DATE_FORMAT_PY


def model_id(tables: Dict[str, Table]) -> Dict[str, str]:
    table = tables.get("MDL_ID")
    if table is None:
        raise Ercot7kCaseError("case has no MDL_ID table")
    records = table.records()
    if len(records) != 1:
        raise Ercot7kCaseError(
            "MDL_ID holds scalar data but has %d rows" % len(records)
        )
    return records[0]


def interval_delta(mdl: Dict[str, str]) -> timedelta:
    unit = (mdl.get("TimeUnit") or "").strip().lower()
    if unit not in TIME_UNIT_SECONDS:
        raise Ercot7kCaseError(
            "MDL_ID.TimeUnit %r is not one of %s"
            % (mdl.get("TimeUnit"), ", ".join(sorted(TIME_UNIT_SECONDS)))
        )
    length = int(float(mdl.get("IntervalLength") or 1))
    return timedelta(seconds=TIME_UNIT_SECONDS[unit] * length)


def model_timepoints(tables: Dict[str, Table]) -> List[str]:
    """Every valid time point, MinDate..MaxDate inclusive, as written text."""
    mdl = model_id(tables)
    fmt = aimms_date_format(tables)
    step = interval_delta(mdl)
    start = datetime.strptime(mdl["MinDate"], fmt)
    stop = datetime.strptime(mdl["MaxDate"], fmt)
    if stop < start:
        raise Ercot7kCaseError("MDL_ID.MaxDate is before MinDate")
    points: List[str] = []
    current = start
    while current <= stop:
        points.append(current.strftime(fmt))
        current = current + step
    return points


# ------------------------------------------------------------------------------
#   Expanders -- the only place study semantics live
# ------------------------------------------------------------------------------
# datacenter_deltas() is the whole of decision D1: the datacenter is a truly
# fixed load, pinned by SCN_INJ_DSP, not a decision variable and not able to
# curtail. BYOG is an ordinary generator at the same node with a scalar cost,
# so behind-the-meter behaviour comes free from siting -- net injection is
# BYOG minus DC load, and a BYOG sized at or below the load can never export.
# ------------------------------------------------------------------------------
def datacenter_deltas(spec: DatacenterSpec,
                      tables: Dict[str, Table]) -> List[Delta]:
    if spec.load_shape != "flat":
        raise NotImplementedError(
            "load_shape %r is not implemented. Only 'flat' exists in "
            "Milestone 1. A shaped datacenter load is an AddSchedule plus a "
            "SCN_INJ_DSP row carrying Schedule instead of Dispatch; the "
            "AddSchedule delta and the SCH_TMP<N> sibling already exist for "
            "exactly that." % (spec.load_shape,)
        )
    if float(spec.byog_p_nom_mw) != float(spec.byog_max_mw):
        raise NotImplementedError(
            "byog_p_nom_mw (%s) != byog_max_mw (%s). A BYOG whose operating "
            "capacity differs from its nameplate ceiling needs a SCN_INJ_MAX "
            "row, and SCN_INJ_MAX is Milestone 2 -- see the SCN_TABLES "
            "registry in this module. Until it lands INJ_ID.MaxMw alone sets "
            "BYOG capacity, so the two must be equal or the sweep is silently "
            "capped at the ceiling (V11)."
            % (spec.byog_p_nom_mw, spec.byog_max_mw)
        )

    scenarios = named_scenarios(tables)
    deltas: List[Delta] = [
        AddInjector(
            injector=spec.load_injector,
            node=spec.node,
            load_flag=True,
            max_mw=float(spec.p_set_mw),
            min_mw=0.0,
            energy_cost=0.0,
            name="%s datacenter load" % spec.dc_name,
        )
    ]
    for scenario in scenarios:
        fields = {"Dispatch": FMT_MW % float(spec.p_set_mw)}
        # Enforce is narrow: it means "Dispatch = 0 should be enforced". A
        # non-zero dispatch is enforced regardless, so it is set only when the
        # pinned MW could be zero.
        if float(spec.p_set_mw) == 0.0:
            fields["Enforce"] = "1"
        deltas.append(
            ScenarioOverride.of(
                "SCN_INJ_DSP", scenario, spec.load_injector, fields
            )
        )
    deltas.append(
        AddInjector(
            injector=spec.byog_injector,
            node=spec.node,
            load_flag=False,
            max_mw=float(spec.byog_max_mw),
            min_mw=0.0,
            energy_cost=float(spec.byog_mc),
            name="%s behind-the-meter generation" % spec.dc_name,
        )
    )
    return deltas


def named_scenarios(tables: Dict[str, Table]) -> List[str]:
    """Scenario names from CYC_SCN, in file order. '0' is the default, not a name."""
    table = tables.get("CYC_SCN")
    if table is None:
        return list(EXPECTED_SCENARIOS)
    names: List[str] = []
    for record in table.records():
        scenario = record.get("Scenario", "")
        if scenario and scenario != "0" and scenario not in names:
            names.append(scenario)
    return names or list(EXPECTED_SCENARIOS)


def stress_deltas(rows: Sequence[Dict[str, str]],
                  tables: Dict[str, Table]) -> List[Delta]:
    raise NotImplementedError(
        "stress_deltas() is Milestone 2. Every stress lever it expands "
        "(k_load via SCN_ARA_LOD, BYOG sweeps via SCN_INJ_MAX) writes to a "
        "table marked supported=False in the SCN_TABLES registry in this "
        "module; adding a lever means one entry there, one referential rule "
        "in verify_case() and one row in the design's merge table."
    )


# ------------------------------------------------------------------------------
#   Row builders
# ------------------------------------------------------------------------------
# Every value that reaches a file goes through one of these, so field formats
# and blank-versus-zero decisions live in one place.
#
# RaiseRR and LowerRR are written as 0.000, which PSO reads as "no ramp limit".
# That is the correct representation of a firm datacenter load: it is pinned by
# SCN_INJ_DSP and never ramps as a decision, so a finite rate here could only
# make the pin infeasible. BYOG gets the same treatment because this study
# varies its capacity and cost, not its ramp.
# ------------------------------------------------------------------------------
def injector_id_values(delta: AddInjector) -> Dict[str, str]:
    return {
        "Injector": delta.injector,
        "Name": delta.name,
        # Area stays BLANK. Never "0" -- that is a dummy injector, outside
        # power balance, and the case would run clean doing nothing. V2.
        "Area": "",
        "LoadFlag": "1" if delta.load_flag else "0",
        "Link": "0",
        "MaxMw": FMT_MW % float(delta.max_mw),
        "MinMw": FMT_MW % float(delta.min_mw),
        "RaiseRR": FMT_MW % 0.0,
        "LowerRR": FMT_MW % 0.0,
        "MinTime": FMT_MW % 0.0,
        "RampSuSd": "0",
        "EnergyCost": FMT_MW % float(delta.energy_cost),
        "CostAdder": FMT_MW % 0.0,
        "RampUpCost": FMT_MW % 0.0,
        "RampDnCost": FMT_MW % 0.0,
    }


def injector_net_values(delta: AddInjector) -> Dict[str, str]:
    return {
        "Injector": delta.injector,
        "Node": delta.node,
        "PhysicalArea": "",
        "LossFactor": FMT_FINE % 0.0,
        "IgnoreLoss": "0",
    }


def schedule_att_values(delta: AddSchedule) -> Dict[str, str]:
    return {
        "Schedule": delta.schedule,
        "RepeatTime": str(int(delta.repeat_time)),
        "StepChange": "1" if delta.step_change else "0",
        "Cycle": "",
        "Periodic": "0",
        "UseMin": "0",
        "UseMax": "0",
        "UseFirst": "0",
        "UseLast": "0",
        "Report": "0",
    }


def schedule_tmp_values(schedule: str, time: str, value: float) -> Dict[str, str]:
    return {
        "Schedule": schedule,
        "Time": time,
        "Value": FMT_MW % float(value),
        # Enforce=1 on every point. All 89,040 base time points carry it, and
        # without it a zero value in the middle of a profile is ignored rather
        # than honoured.
        "Enforce": "1",
    }


# ------------------------------------------------------------------------------
#   Layer writer
# ------------------------------------------------------------------------------
# Ownership rule, one line: adds are exclusive; edits are permitted but must
# declare the value they expect to replace. This milestone implements adds
# only, so every collision is an error.
# ------------------------------------------------------------------------------
@dataclass
class _LayerBuild:
    tables: Dict[str, Table]
    prefix: str
    owned: List[Dict[str, Any]] = field(default_factory=list)
    owned_files: List[str] = field(default_factory=list)
    changed_files: Dict[str, List[str]] = field(default_factory=dict)
    new_tables: Dict[str, Table] = field(default_factory=dict)
    capacity_ceilings: Dict[str, float] = field(default_factory=dict)

    def filename_for(self, table_name: str) -> str:
        if table_name == CONTROL_TABLE:
            return "%s.csv" % self.prefix
        return "%s_%s.csv" % (self.prefix, table_name)

    def record_owned(self, table: str, key: Sequence[str], op: str,
                     fields: Dict[str, str]) -> None:
        self.owned.append({
            "table": table,
            "file": self.filename_for(table),
            "key": list(key),
            "op": op,
            "fields": dict(fields),
            "prior_owner": None,
            "prior_values": None,
        })

    def append(self, table_name: str, values: Dict[str, str]) -> None:
        table = self.tables[table_name]
        line = table.append_record(values)
        if table_name in self.new_tables:
            return
        self.changed_files.setdefault(table.filename, []).append(line)


def _existing_keys(table: Table, key_fields: Sequence[str]) -> Dict[Tuple[str, ...], int]:
    keys: Dict[Tuple[str, ...], int] = {}
    for index, record in enumerate(table.records()):
        keys[tuple(record[f] for f in key_fields)] = index
    return keys


def _check_add_collision(build: _LayerBuild, table_name: str,
                         key_fields: Sequence[str], key: Tuple[str, ...],
                         owned_by: Dict[Tuple[str, Tuple[str, ...]], str]) -> None:
    table = build.tables.get(table_name)
    if table is not None and key in _existing_keys(table, key_fields):
        owner = owned_by.get((table_name, key))
        where = " (owned by layer %s)" % owner if owner else " (present in the parent case)"
        raise OwnershipError(
            "%s key %s already exists%s. Adds are exclusive: duplicate primary "
            "keys COALESCE non-blank fields in PSO rather than last-row-wins, "
            "so two definitions would silently merge into one."
            % (table_name, "/".join(key), where)
        )
    owner = owned_by.get((table_name, key))
    if owner:
        raise OwnershipError(
            "%s key %s is owned by layer %s. Adds are exclusive."
            % (table_name, "/".join(key), owner)
        )


# ------------------------------------------------------------------------------
# apply_deltas()
#
# Applies a delta list to an in-memory copy of the parent's tables, returning
# the build state the manifest and the writer both read from. Nothing touches
# the filesystem here.
# ------------------------------------------------------------------------------
def apply_deltas(tables: Dict[str, Table], deltas: Sequence[Delta],
                 prefix: str,
                 owned_by: Optional[Dict[Tuple[str, Tuple[str, ...]], str]] = None,
                 ) -> _LayerBuild:
    owned_by = owned_by or {}
    build = _LayerBuild(tables=tables, prefix=prefix)

    nodes = {r["Enode"] for r in tables["NDE_ID"].records()} if "NDE_ID" in tables else set()
    scenarios = set(named_scenarios(tables)) | {"0"}
    timepoints = model_timepoints(tables)
    schedule_deltas = [d for d in deltas if isinstance(d, AddSchedule)]

    if schedule_deltas:
        _create_sch_tmp_sibling(build)

    for delta in deltas:
        if isinstance(delta, AddInjector):
            _apply_add_injector(build, delta, nodes, owned_by)
        elif isinstance(delta, AddSchedule):
            _apply_add_schedule(build, delta, timepoints, owned_by)
        elif isinstance(delta, ScenarioOverride):
            _apply_scenario_override(build, delta, scenarios, owned_by)
        elif isinstance(delta, FieldEdit):
            raise NotImplementedError(
                "FieldEdit is not implemented. It is the escape hatch for "
                "editing an existing row in place (BRN_ID.Monitor, a "
                "texas7k.csv OptionValue), and it must declare the value it "
                "expects to replace before it can be allowed to. The "
                "extension point is apply_deltas() in this module, plus a "
                "'prior_values' entry in the manifest's owned list and the "
                "edit branch of V9. Delta was: %r" % (delta,)
            )
        else:
            raise Ercot7kCaseError("unknown delta type %r" % (delta,))
    return build


def _create_sch_tmp_sibling(build: _LayerBuild) -> None:
    existing = sch_tmp_tables(build.tables)
    if not existing:
        raise Ercot7kCaseError(
            "case has no SCH_TMP file to take a header from"
        )
    index = next_sch_tmp_index(build.tables)
    name = "SCH_TMP%d" % index
    filename = build.filename_for(name)
    # SCH_TMP1 is copied byte-identical and never parsed for content. The one
    # thing taken from it is its header line, which is exactly the schema
    # introspection rule: the new sibling's header IS the old sibling's header.
    header = existing[0][1].raw_lines[0]
    table = Table(name=name, filename=filename, raw_lines=[header])
    build.tables[name] = table
    build.new_tables[name] = table
    build.owned_files.append(filename)


def _apply_add_injector(build: _LayerBuild, delta: AddInjector,
                        nodes: set,
                        owned_by: Dict[Tuple[str, Tuple[str, ...]], str]) -> None:
    if not delta.injector:
        raise Ercot7kCaseError("AddInjector requires an injector name")
    if delta.node and nodes and delta.node not in nodes:
        raise Ercot7kCaseError(
            "AddInjector node %r is not in NDE_ID.Enode. An injector mapped to "
            "an unknown node falls back to area load distribution, silently "
            "putting the datacenter somewhere else." % delta.node
        )
    key = (delta.injector,)
    _check_add_collision(build, "INJ_ID", ("Injector",), key, owned_by)
    _check_add_collision(build, "INJ_NET", ("Injector",), key, owned_by)

    id_values = injector_id_values(delta)
    net_values = injector_net_values(delta)
    build.append("INJ_ID", id_values)
    build.record_owned("INJ_ID", key, "append", id_values)
    build.append("INJ_NET", net_values)
    build.record_owned("INJ_NET", key, "append", net_values)
    if not delta.load_flag:
        build.capacity_ceilings[delta.injector] = float(delta.max_mw)


def _apply_add_schedule(build: _LayerBuild, delta: AddSchedule,
                        timepoints: Sequence[str],
                        owned_by: Dict[Tuple[str, Tuple[str, ...]], str]) -> None:
    if len(delta.values) != len(timepoints):
        raise Ercot7kCaseError(
            "AddSchedule %r has %d values but the model horizon has %d time "
            "points (MDL_ID.MinDate..MaxDate inclusive). A schedule short of "
            "MaxDate reads as ZERO past its last point, not hold-last, and SC "
            "carries 48 h of lead time."
            % (delta.schedule, len(delta.values), len(timepoints))
        )
    if int(delta.repeat_time) != len(timepoints):
        raise Ercot7kCaseError(
            "AddSchedule %r has repeat_time %s but the model horizon has %d "
            "time points. Every one of the 336 base schedules sets RepeatTime "
            "to its own time-point count; the default of 265 is the ercot7k "
            "horizon, so a caller on any other horizon has to say so rather "
            "than inherit a repeat window that does not fit."
            % (delta.schedule, delta.repeat_time, len(timepoints))
        )
    _check_add_collision(build, "SCH_ATT", ("Schedule",),
                         (delta.schedule,), owned_by)
    for index, table in sch_tmp_tables(build.tables):
        if table.name in build.new_tables:
            continue
        for record in table.records():
            if record["Schedule"] == delta.schedule:
                raise OwnershipError(
                    "schedule %r already has time points in %s"
                    % (delta.schedule, table.filename)
                )

    att_values = schedule_att_values(delta)
    build.append("SCH_ATT", att_values)
    build.record_owned("SCH_ATT", (delta.schedule,), "append", att_values)

    sibling = [t for name, t in build.new_tables.items()
               if name.startswith("SCH_TMP")]
    if not sibling:
        raise Ercot7kCaseError("no SCH_TMP sibling was created for this layer")
    target = sibling[0]
    for time, value in zip(timepoints, delta.values):
        values = schedule_tmp_values(delta.schedule, time, value)
        target.append_record(values)
        build.record_owned(target.name, (delta.schedule, time),
                           "append", values)


def _apply_scenario_override(build: _LayerBuild, delta: ScenarioOverride,
                             scenarios: set,
                             owned_by: Dict[Tuple[str, Tuple[str, ...]], str]) -> None:
    entry = SCN_TABLES.get(delta.table)
    if entry is None:
        raise NotImplementedError(
            "ScenarioOverride on %r is not implemented. Scenario tables are "
            "declared in the SCN_TABLES registry in this module; adding one "
            "costs one entry there, one referential rule in verify_case() and "
            "one row in the design's merge table. Implemented now: %s"
            % (delta.table,
               ", ".join(sorted(k for k, v in SCN_TABLES.items() if v["supported"])))
        )
    if not entry["supported"]:
        raise NotImplementedError(
            "ScenarioOverride on %r is Milestone %s, not implemented here. %s "
            "The extension point is the SCN_TABLES registry in this module."
            % (delta.table, entry.get("milestone"), entry.get("note", ""))
        )
    if delta.scenario == "*":
        raise NotImplementedError(
            "the '*' all-rows scenario form is only meaningful for "
            "SCN_ARA_LOD, which is Milestone 2. See the SCN_TABLES registry."
        )
    if delta.scenario not in scenarios:
        raise Ercot7kCaseError(
            "scenario %r is neither '0' nor a CYC_SCN.Scenario (%s)"
            % (delta.scenario, ", ".join(sorted(s for s in scenarios if s != "0")))
        )

    key_fields = entry["key_fields"]
    key = (delta.scenario, delta.key)
    if delta.table not in build.tables:
        _create_new_scn_table(build, delta.table)
    _check_add_collision(build, delta.table, key_fields, key, owned_by)

    values = {key_fields[0]: delta.scenario, key_fields[1]: delta.key}
    values.update(delta.field_map())
    build.append(delta.table, values)
    build.record_owned(delta.table, key, "create-or-append", values)


def _create_new_scn_table(build: _LayerBuild, table_name: str) -> None:
    columns = NEW_TABLE_COLUMNS.get(table_name)
    if columns is None:
        raise Ercot7kCaseError(
            "%s does not exist in the parent case and this module has no "
            "authored header for it" % table_name
        )
    filename = build.filename_for(table_name)
    # LF, matching 23 of the 24 base files. The one CRLF file (CYC_SAI) is
    # copied verbatim and is not a precedent for new files.
    header = ",".join(columns) + "\n"
    table = Table(name=table_name, filename=filename, raw_lines=[header])
    build.tables[table_name] = table
    build.new_tables[table_name] = table
    build.owned_files.append(filename)


# ------------------------------------------------------------------------------
# write_layer()
#
# Emits a derived case directory. Files the deltas did not touch are copied
# byte for byte, not re-serialized: reserialization is what turns 746.000 into
# 746.0 across 634 rows, and "immutable base" has to mean something.
#
# The manifest is written once, in final form, with verification already
# folded in. If verify_case() returns an ERROR the directory is removed and
# nothing is emitted.
# ------------------------------------------------------------------------------
def write_layer(base_dir: Path, out_dir: Path, deltas: Sequence[Delta],
                study: Optional[Dict[str, Any]] = None,
                slug: str = "layer",
                strict_monitored: bool = False) -> Dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    out_dir = Path(out_dir).resolve()
    if out_dir.exists():
        raise Ercot7kCaseError("%s already exists" % out_dir)
    if out_dir == base_dir:
        raise Ercot7kCaseError("the output directory is the base directory")

    chain = walk_chain(base_dir)
    owned_by = chain_owned_keys(chain)
    prefix = case_prefix(base_dir)
    tables = read_case(base_dir, prefix)

    build = apply_deltas(tables, deltas, prefix, owned_by)

    ceilings = chain_capacity_ceilings(chain)
    ceilings.update(build.capacity_ceilings)

    out_dir.mkdir(parents=True)
    try:
        changed = set(build.changed_files)
        new_files = set(build.owned_files)
        for source in case_files(base_dir):
            if source.name in changed:
                name = table_name_for(source.name, prefix)
                write_table(build.tables[name], out_dir / source.name)
            else:
                shutil.copyfile(source, out_dir / source.name)
        for name, table in build.new_tables.items():
            write_table(table, out_dir / table.filename)

        manifest = _build_manifest(
            base_dir=base_dir, out_dir=out_dir, chain=chain, prefix=prefix,
            build=build, study=study, slug=slug, ceilings=ceilings,
        )
        findings = verify_case(out_dir, strict_monitored=strict_monitored,
                               manifest=manifest)
        manifest["verification"] = {
            "verified_utc": _utc_now(),
            "strict_monitored": bool(strict_monitored),
            "findings": [f.as_dict() for f in findings],
        }
        errors = [f for f in findings if f.level == LEVEL_ERROR]
        if errors:
            raise VerificationError(
                "verify_case rejected the layer:\n%s"
                % "\n".join("  %s %s: %s" % (f.level, f.check, f.message)
                            for f in errors)
            )
        write_manifest(out_dir, manifest)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return manifest


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_manifest(base_dir: Path, out_dir: Path, chain: Sequence[ChainLayer],
                    prefix: str, build: _LayerBuild,
                    study: Optional[Dict[str, Any]], slug: str,
                    ceilings: Dict[str, float]) -> Dict[str, Any]:
    parent_layer = chain[-1]
    parent_manifest_file = manifest_path(base_dir)
    parent = {
        "kind": parent_layer.kind,
        "path": str(base_dir),
        "layer_id": parent_layer.layer_id,
        "manifest_sha256": (sha256_file(parent_manifest_file)
                            if parent_manifest_file.is_file() else None),
        "self_sha256": case_digest(base_dir),
    }
    self_sha256 = case_digest(out_dir)
    layer_id = "%s-%s" % (slug, self_sha256[:8])

    tables = build.tables
    mdl = model_id(tables)
    cycles = [r["Cycle"] for r in tables["CYC_ID"].records()] if "CYC_ID" in tables else []
    return {
        "schema": MANIFEST_SCHEMA,
        "layer_id": layer_id,
        "created_utc": _utc_now(),
        "writer": WRITER_ID,
        "parent": parent,
        "self_sha256": self_sha256,
        "case": {
            "control_file": "%s.csv" % prefix,
            "prefix": prefix,
            "mdl_id": dict(mdl),
            "cycles": cycles,
            "scenarios": named_scenarios(tables),
            # The reported (cycle, scenario) triple must be pinned by the
            # study, not recomputed per run: a k_load change moves the peak
            # hour and you end up comparing hour 88 against hour 91.
            "report_cycle": "RT",
            "report_scenario": "ScnRT",
        },
        "study": study or {"datacenters": [], "stress": []},
        "capacity_ceilings": ceilings,
        "owned": build.owned,
        "owned_files": sorted(build.owned_files),
        "changed_files": {name: lines
                          for name, lines in sorted(build.changed_files.items())},
        "deliberate_omissions": [dict(entry) for entry in DELIBERATE_OMISSIONS],
        "verification": None,
    }


# ------------------------------------------------------------------------------
# build_datacenter_layer()
#
# The Milestone 1 deliverable in one call: a complete, verified datacenter
# layer written from a parent case.
# ------------------------------------------------------------------------------
def build_datacenter_layer(base_dir: Path, out_dir: Path,
                           spec: DatacenterSpec,
                           strict_monitored: bool = False) -> Dict[str, Any]:
    base_dir = Path(base_dir)
    tables = read_case(base_dir)
    deltas = datacenter_deltas(spec, tables)
    timepoints = model_timepoints(tables)
    study = {
        "datacenters": [{
            "dc_name": spec.dc_name,
            "node": spec.node,
            "p_set_mw": float(spec.p_set_mw),
            "load_injector": spec.load_injector,
            "byog_injector": spec.byog_injector,
            "byog_p_nom_mw": float(spec.byog_p_nom_mw),
            "byog_max_mw": float(spec.byog_max_mw),
            "byog_mc": float(spec.byog_mc),
            "load_shape": spec.load_shape,
            "pin_mechanism": "scn_inj_dsp_fixed",
            "expected_dc_mw_by_interval": {
                time: float(spec.p_set_mw) for time in timepoints
            },
        }],
        "stress": [],
    }
    return write_layer(base_dir, out_dir, deltas, study=study,
                       slug=spec.dc_name.lower(),
                       strict_monitored=strict_monitored)


def layer_dir_name(parent_dir: Path, slug: str) -> str:
    """Layer directories are named <parent>__<slug> so a chain reads from ls."""
    return "%s__%s" % (Path(parent_dir).name, slug)


# ------------------------------------------------------------------------------
#   verify_case -- V1 to V13, no PSO required
# ------------------------------------------------------------------------------
# Runs standalone on ANY case directory, including ercot7k/ itself. A check
# that cannot fail on the input it was given reports SKIP with the reason, not
# OK: a skipped check that reads as a pass is how a missing check hides.
# ------------------------------------------------------------------------------
@dataclass
class Finding:
    check: str
    level: str
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {"check": self.check, "level": self.level, "message": self.message}

    def __str__(self) -> str:
        return "%-7s %-3s %s" % (self.level, self.check, self.message)


@dataclass
class _VerifyContext:
    case_dir: Path
    prefix: str
    tables: Dict[str, Table]
    manifest: Optional[Dict[str, Any]]
    strict_monitored: bool
    records: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

    def rec(self, table_name: str) -> List[Dict[str, str]]:
        if table_name not in self.records:
            table = self.tables.get(table_name)
            self.records[table_name] = table.records() if table else []
        return self.records[table_name]

    def owned(self, table_name: str) -> List[Dict[str, Any]]:
        if not self.manifest:
            return []
        return [e for e in self.manifest.get("owned", [])
                if e["table"] == table_name]


def verify_case(case_dir: Path, strict_monitored: bool = False,
                manifest: Optional[Dict[str, Any]] = None) -> List[Finding]:
    case_dir = Path(case_dir)
    prefix = case_prefix(case_dir)
    tables = read_case(case_dir, prefix)
    if manifest is None and has_manifest(case_dir):
        manifest = read_manifest(case_dir)
    ctx = _VerifyContext(case_dir=case_dir, prefix=prefix, tables=tables,
                         manifest=manifest, strict_monitored=strict_monitored)
    findings: List[Finding] = []
    for check in (_v1_referential, _v2_area_blank, _v3_key_uniqueness,
                  _v4_schedule_span, _v5_scenario_rows, _v6_area_load_scale,
                  _v7_injector_domain, _v8_monitored_branch, _v9_parent_diff,
                  _v10_file_set, _v11_capacity_ceiling, _v12_cost_curve_overlap,
                  _v13_cost_range):
        findings.extend(check(ctx))
    return findings


def has_errors(findings: Sequence[Finding]) -> bool:
    return any(f.level == LEVEL_ERROR for f in findings)


def format_findings(findings: Sequence[Finding]) -> str:
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.level] = counts.get(finding.level, 0) + 1
    lines = [str(f) for f in findings]
    lines.append(SUBSECTION_SEPARATOR.rstrip("\n"))
    lines.append("  ".join("%s=%d" % (level, counts.get(level, 0))
                           for level in (LEVEL_OK, LEVEL_SKIP,
                                         LEVEL_WARNING, LEVEL_ERROR)))
    return "\n".join(lines)


# ------------------------------------------------------------------------------
# V1 -- referential integrity
#
# A typo'd node does not error: the injector silently falls back to area load
# distribution and ends up somewhere else entirely. A typo'd schedule name
# reads as absent, which is a zero profile.
# ------------------------------------------------------------------------------
def _v1_referential(ctx: _VerifyContext) -> List[Finding]:
    out: List[Finding] = []
    injectors = {r["Injector"] for r in ctx.rec("INJ_ID")}
    nodes = {r["Enode"] for r in ctx.rec("NDE_ID")}
    scenarios = {"0"} | {r["Scenario"] for r in ctx.rec("CYC_SCN") if r["Scenario"]}
    schedules = {r["Schedule"] for r in ctx.rec("SCH_ATT")}
    curves = {r["CostCurve"] for r in ctx.rec("CCV_ATT")}

    bad_nodes = sorted({r["Node"] for r in ctx.rec("INJ_NET")
                        if r["Node"] and r["Node"] not in nodes})
    if bad_nodes:
        out.append(Finding("V1", LEVEL_ERROR,
                           "INJ_NET.Node not in NDE_ID.Enode: %s"
                           % ", ".join(bad_nodes[:10])))
    bad = sorted({r["Injector"] for r in ctx.rec("INJ_NET")} - injectors)
    if bad:
        out.append(Finding("V1", LEVEL_ERROR,
                           "INJ_NET.Injector not in INJ_ID: %s"
                           % ", ".join(bad[:10])))

    for table_name in sorted(SCN_TABLES):
        rows = ctx.rec(table_name)
        if not rows:
            continue
        if "Injector" in ctx.tables[table_name].columns:
            bad = sorted({r["Injector"] for r in rows} - injectors)
            if bad:
                out.append(Finding("V1", LEVEL_ERROR,
                                   "%s.Injector not in INJ_ID: %s"
                                   % (table_name, ", ".join(bad[:10]))))
        bad = sorted({r["Scenario"] for r in rows} - scenarios)
        if bad:
            out.append(Finding("V1", LEVEL_ERROR,
                               "%s.Scenario is neither '0' nor a CYC_SCN "
                               "scenario: %s" % (table_name, ", ".join(bad[:10]))))
        if "Schedule" in ctx.tables[table_name].columns:
            bad = sorted({r["Schedule"] for r in rows if r["Schedule"]} - schedules)
            if bad:
                out.append(Finding("V1", LEVEL_ERROR,
                                   "%s.Schedule not in SCH_ATT: %s"
                                   % (table_name, ", ".join(bad[:10]))))

    bad = sorted({r["Injector"] for r in ctx.rec("CYC_INJ_CCV")} - injectors)
    if bad:
        out.append(Finding("V1", LEVEL_ERROR,
                           "CYC_INJ_CCV.Injector not in INJ_ID: %s"
                           % ", ".join(bad[:10])))
    bad = sorted({r["CostCurve"] for r in ctx.rec("CYC_INJ_CCV")
                  if r["CostCurve"]} - curves)
    if bad:
        out.append(Finding("V1", LEVEL_ERROR,
                           "CYC_INJ_CCV.CostCurve not in CCV_ATT: %s"
                           % ", ".join(bad[:10])))

    branch_nodes: set = set()
    for record in ctx.rec("BRN_ID"):
        branch_nodes.add(record["FrEnode"])
        branch_nodes.add(record["ToEnode"])
    bad = sorted(n for n in branch_nodes - nodes if n)
    if bad:
        out.append(Finding("V1", LEVEL_ERROR,
                           "BRN_ID endpoint not in NDE_ID.Enode: %s"
                           % ", ".join(bad[:10])))

    scheduled: set = set()
    for _, table in sch_tmp_tables(ctx.tables):
        scheduled |= {r["Schedule"] for r in table.records()}
    missing = sorted(schedules - scheduled)
    if missing:
        out.append(Finding("V1", LEVEL_ERROR,
                           "SCH_ATT schedules with no SCH_TMP time points: %s"
                           % ", ".join(missing[:10])))
    orphan = sorted(scheduled - schedules)
    if orphan:
        out.append(Finding("V1", LEVEL_ERROR,
                           "SCH_TMP schedules absent from SCH_ATT: %s"
                           % ", ".join(orphan[:10])))

    if not out:
        out.append(Finding("V1", LEVEL_OK,
                           "referential integrity holds over %d injectors, "
                           "%d nodes, %d schedules"
                           % (len(injectors), len(nodes), len(schedules))))
    return out


# ------------------------------------------------------------------------------
# V2 -- appended INJ_ID rows carry a BLANK Area
#
# The most expensive mistake available in this component. Area="0" makes the
# injector a "dummy" that sits outside power balance: the case runs clean, the
# reports look normal, and the datacenter does nothing at all.
# ------------------------------------------------------------------------------
def _v2_area_blank(ctx: _VerifyContext) -> List[Finding]:
    owned = ctx.owned("INJ_ID")
    if not owned:
        return [Finding("V2", LEVEL_SKIP,
                        "no manifest-owned INJ_ID rows in this directory")]
    by_key = {r["Injector"]: r for r in ctx.rec("INJ_ID")}
    bad: List[str] = []
    for entry in owned:
        injector = entry["key"][0]
        record = by_key.get(injector)
        if record is None:
            bad.append("%s: owned but not present" % injector)
        elif record["Area"] != "":
            bad.append("%s: Area=%r, must be blank" % (injector, record["Area"]))
    if bad:
        return [Finding("V2", LEVEL_ERROR,
                        "appended INJ_ID rows must have a blank Area (Area=0 "
                        "is a dummy injector outside power balance): %s"
                        % "; ".join(bad))]
    return [Finding("V2", LEVEL_OK,
                    "%d appended INJ_ID row(s) carry a blank Area" % len(owned))]


# ------------------------------------------------------------------------------
# V3 -- primary-key uniqueness per table, including across SCH_TMP1..N
#
# Duplicate primary keys COALESCE non-blank fields across rows rather than
# last-row-wins, so two definitions of one key merge into a third thing that
# neither row says.
# ------------------------------------------------------------------------------
def _v3_key_uniqueness(ctx: _VerifyContext) -> List[Finding]:
    out: List[Finding] = []
    checked = 0
    skipped: List[str] = []

    for name in sorted(ctx.tables):
        if name.startswith("SCH_TMP"):
            continue
        key_fields = TABLE_KEYS.get(name)
        if key_fields is None:
            skipped.append(name)
            continue
        columns = ctx.tables[name].columns
        if not all(f in columns for f in key_fields):
            skipped.append(name)
            continue
        checked += 1
        seen: Dict[Tuple[str, ...], int] = {}
        dups: List[str] = []
        for record in ctx.rec(name):
            key = tuple(record[f] for f in key_fields)
            if key in seen:
                dups.append("/".join(key))
            seen[key] = 1
        if dups:
            out.append(Finding("V3", LEVEL_ERROR,
                               "%s has duplicate primary key(s) %s; duplicate "
                               "keys coalesce non-blank fields rather than "
                               "last-row-wins"
                               % (name, ", ".join(sorted(set(dups))[:10]))))

    tmp_tables = sch_tmp_tables(ctx.tables)
    if tmp_tables:
        checked += 1
        seen_tmp: Dict[Tuple[str, str], str] = {}
        dups = []
        for _, table in tmp_tables:
            for record in table.records():
                key = (record["Schedule"], record["Time"])
                if key in seen_tmp:
                    dups.append("%s/%s in %s and %s"
                                % (key[0], key[1], seen_tmp[key], table.filename))
                seen_tmp[key] = table.filename
        if dups:
            out.append(Finding("V3", LEVEL_ERROR,
                               "duplicate (Schedule, Time) across SCH_TMP "
                               "siblings: %s" % "; ".join(sorted(set(dups))[:10])))

    if skipped:
        out.append(Finding("V3", LEVEL_SKIP,
                           "no documented primary key for: %s"
                           % ", ".join(sorted(skipped))))
    if not any(f.level == LEVEL_ERROR for f in out):
        out.append(Finding("V3", LEVEL_OK,
                           "primary keys unique across %d table(s)" % checked))
    return out


# ------------------------------------------------------------------------------
# V4 -- schedules span MinDate..MaxDate inclusive with Enforce=1
#
# NOT StartDate..StopDate. Values past a schedule's last point read as ZERO,
# not hold-last, and SC has 48 h of lead time while DA looks 24 h ahead. A
# schedule that stops at StopDate switches off inside the lead window.
# ------------------------------------------------------------------------------
def _v4_schedule_span(ctx: _VerifyContext) -> List[Finding]:
    tmp_tables = sch_tmp_tables(ctx.tables)
    if not tmp_tables or "MDL_ID" not in ctx.tables:
        return [Finding("V4", LEVEL_SKIP, "no SCH_TMP or no MDL_ID")]
    timepoints = model_timepoints(ctx.tables)
    expected = len(timepoints)
    first_expected, last_expected = timepoints[0], timepoints[-1]

    points: Dict[str, List[str]] = {}
    unenforced: Dict[str, int] = {}
    for _, table in tmp_tables:
        for record in table.records():
            schedule = record["Schedule"]
            points.setdefault(schedule, []).append(record["Time"])
            if record.get("Enforce") != "1":
                unenforced[schedule] = unenforced.get(schedule, 0) + 1

    bad: List[str] = []
    for schedule in sorted(points):
        times = points[schedule]
        if len(times) != expected:
            bad.append("%s has %d time points, expected %d"
                       % (schedule, len(times), expected))
            continue
        if min(times) != first_expected:
            bad.append("%s starts at %s, expected MDL_ID.MinDate %s"
                       % (schedule, min(times), first_expected))
        if max(times) != last_expected:
            bad.append("%s ends at %s, expected MDL_ID.MaxDate %s"
                       % (schedule, max(times), last_expected))
    for schedule in sorted(unenforced):
        bad.append("%s has %d time point(s) without Enforce=1"
                   % (schedule, unenforced[schedule]))
    if bad:
        return [Finding("V4", LEVEL_ERROR,
                        "schedules must span MDL_ID.MinDate..MaxDate "
                        "inclusive (%d points) with Enforce=1: %s"
                        % (expected, "; ".join(bad[:10])))]
    return [Finding("V4", LEVEL_OK,
                    "%d schedule(s) span %s..%s at %d points with Enforce=1"
                    % (len(points), first_expected, last_expected, expected))]


# ------------------------------------------------------------------------------
# V5 -- SCN_INJ_DSP carries a row for every named scenario
#
# A missing scenario row is a silently switched-off datacenter: "outaged status
# is assumed for any scenario that does not have a dispatch schedule".
# ------------------------------------------------------------------------------
def _v5_scenario_rows(ctx: _VerifyContext) -> List[Finding]:
    rows = ctx.rec("SCN_INJ_DSP")
    if not rows:
        return [Finding("V5", LEVEL_SKIP,
                        "no SCN_INJ_DSP table in this directory")]
    named = named_scenarios(ctx.tables)
    have: Dict[str, set] = {}
    for record in rows:
        have.setdefault(record["Injector"], set()).add(record["Scenario"])
    bad: List[str] = []
    for injector in sorted(have):
        missing = [s for s in named if s not in have[injector]]
        if missing:
            bad.append("%s missing %s" % (injector, ", ".join(missing)))
    if bad:
        return [Finding("V5", LEVEL_ERROR,
                        "every fixed-dispatch injector needs a row for every "
                        "named scenario (%s); a missing row is a silently "
                        "switched-off datacenter: %s"
                        % (", ".join(named), "; ".join(bad)))]
    return [Finding("V5", LEVEL_OK,
                    "%d fixed-dispatch injector(s) carry rows for %s"
                    % (len(have), ", ".join(named)))]


# ------------------------------------------------------------------------------
# V6 -- SCN_ARA_LOD.ScaleFactor reaches every row, and is never zero
#
# The write itself is Milestone 2. The check is wired now because a zero
# ScaleFactor silently becomes 1, and a factor written only to the default
# scenario '0' leaves ScnRT -- the reported cycle -- at 1.0, making the lever
# look almost inert.
# ------------------------------------------------------------------------------
def _v6_area_load_scale(ctx: _VerifyContext) -> List[Finding]:
    rows = ctx.rec("SCN_ARA_LOD")
    if not rows:
        return [Finding("V6", LEVEL_SKIP, "no SCN_ARA_LOD table")]
    out: List[Finding] = []
    zeros = [r for r in rows if r.get("ScaleFactor", "").strip() not in ("",)
             and _as_float(r["ScaleFactor"]) == 0.0]
    if zeros:
        out.append(Finding("V6", LEVEL_ERROR,
                           "SCN_ARA_LOD.ScaleFactor is 0 on %d row(s); a zero "
                           "scale factor is silently read as 1"
                           % len(zeros)))
    owned = ctx.owned("SCN_ARA_LOD")
    if owned:
        intended = {e["fields"].get("ScaleFactor") for e in owned}
        if len(intended) == 1:
            wanted = intended.pop()
            missing = [r for r in rows if r.get("ScaleFactor") != wanted]
            if missing:
                out.append(Finding("V6", LEVEL_ERROR,
                                   "%d SCN_ARA_LOD row(s) do not carry the "
                                   "intended ScaleFactor %s; non-default "
                                   "scenarios without one are assigned 1"
                                   % (len(missing), wanted)))
    else:
        out.append(Finding("V6", LEVEL_SKIP,
                           "no manifest-owned SCN_ARA_LOD rows (k_load is "
                           "Milestone 2); checked %d existing row(s) for a "
                           "zero ScaleFactor only" % len(rows)))
    if not any(f.level == LEVEL_ERROR for f in out) and owned:
        out.append(Finding("V6", LEVEL_OK,
                           "every SCN_ARA_LOD row carries the intended "
                           "ScaleFactor"))
    return out


# ------------------------------------------------------------------------------
# V7 -- INJ_ID domain: MinMw <= 0, MaxMw > 0, LoadFlag in {0,1}
# ------------------------------------------------------------------------------
def _v7_injector_domain(ctx: _VerifyContext) -> List[Finding]:
    rows = ctx.rec("INJ_ID")
    if not rows:
        return [Finding("V7", LEVEL_SKIP, "no INJ_ID table")]
    bad: List[str] = []
    for record in rows:
        injector = record["Injector"]
        if _as_float(record.get("MaxMw", "")) <= 0.0:
            bad.append("%s MaxMw=%s must be positive"
                       % (injector, record.get("MaxMw")))
        if _as_float(record.get("MinMw", "")) > 0.0:
            bad.append("%s MinMw=%s must be zero or negative"
                       % (injector, record.get("MinMw")))
        if (record.get("LoadFlag") or "0") not in ("0", "1"):
            bad.append("%s LoadFlag=%s must be 0 or 1"
                       % (injector, record.get("LoadFlag")))
    if bad:
        return [Finding("V7", LEVEL_ERROR,
                        "INJ_ID rows out of spec: %s" % "; ".join(bad[:10]))]
    return [Finding("V7", LEVEL_OK,
                    "%d INJ_ID row(s) within MinMw<=0, MaxMw>0, LoadFlag in "
                    "{0,1}" % len(rows))]


# ------------------------------------------------------------------------------
# V8 -- the datacenter node is on a monitored branch
#
# Only 1171 of 9140 branches carry Monitor=1, and "when Monitor is flagged,
# flows are not calculated and limit is not enforced unless feasibility
# analysis is used". A datacenter at an unmonitored bus creates congestion the
# model never reports: the case looks clean and is wrong.
# ------------------------------------------------------------------------------
def _v8_monitored_branch(ctx: _VerifyContext) -> List[Finding]:
    owned = ctx.owned("INJ_NET")
    if not owned:
        return [Finding("V8", LEVEL_SKIP,
                        "no manifest-owned INJ_NET rows in this directory")]
    monitored: set = set()
    for record in ctx.rec("BRN_ID"):
        if record.get("Monitor") == "1":
            monitored.add(record["FrEnode"])
            monitored.add(record["ToEnode"])
    by_key = {r["Injector"]: r for r in ctx.rec("INJ_NET")}
    unmonitored: List[str] = []
    for entry in owned:
        injector = entry["key"][0]
        node = (by_key.get(injector) or {}).get("Node", "")
        if node and node not in monitored:
            unmonitored.append("%s at %s" % (injector, node))
    if unmonitored:
        level = LEVEL_ERROR if ctx.strict_monitored else LEVEL_WARNING
        return [Finding("V8", level,
                        "added injector(s) sit at nodes touched only by "
                        "Monitor=0 branches, so any congestion they create is "
                        "never reported: %s" % "; ".join(unmonitored))]
    return [Finding("V8", LEVEL_OK,
                    "%d added injector(s) sit on monitored branches"
                    % len(owned))]


# ------------------------------------------------------------------------------
# V9 -- byte-level diff against the parent
#
# Headers identical, unchanged files byte-identical, changed files differing
# ONLY in the lines the manifest declares. This is what makes "immutable base"
# mean something: it is the check that catches reserialization (746.000 ->
# 746.0 across 634 rows), column reordering, CRLF, a BOM, a lost trailing
# newline.
# ------------------------------------------------------------------------------
def _v9_parent_diff(ctx: _VerifyContext) -> List[Finding]:
    if not ctx.manifest:
        return [Finding("V9", LEVEL_SKIP, "no manifest, so no parent to diff")]
    parent_dir = Path(ctx.manifest["parent"]["path"])
    if not parent_dir.is_dir():
        return [Finding("V9", LEVEL_ERROR,
                        "parent directory %s does not exist" % parent_dir)]
    declared = ctx.manifest.get("changed_files", {})
    owned_files = set(ctx.manifest.get("owned_files", []))
    out: List[Finding] = []
    unchanged = 0
    for source in case_files(parent_dir):
        target = ctx.case_dir / source.name
        if not target.is_file():
            out.append(Finding("V9", LEVEL_ERROR,
                               "%s is present in the parent and missing here"
                               % source.name))
            continue
        if source.name in declared:
            parent_lines = _split_lines_keepends(
                source.read_bytes().decode("ascii"))
            child_lines = _split_lines_keepends(
                target.read_bytes().decode("ascii"))
            added = declared[source.name]
            if child_lines != parent_lines + added:
                out.append(Finding("V9", LEVEL_ERROR,
                                   "%s differs from the parent in lines the "
                                   "manifest does not declare (parent %d lines, "
                                   "here %d, declared %d added)"
                                   % (source.name, len(parent_lines),
                                      len(child_lines), len(added))))
        else:
            if source.read_bytes() != target.read_bytes():
                out.append(Finding("V9", LEVEL_ERROR,
                                   "%s was not declared as changed but differs "
                                   "byte for byte from the parent"
                                   % source.name))
            else:
                unchanged += 1
    for name in sorted(owned_files):
        target = ctx.case_dir / name
        if not target.is_file():
            out.append(Finding("V9", LEVEL_ERROR,
                               "declared owned file %s was not written" % name))
    if not out:
        out.append(Finding("V9", LEVEL_OK,
                           "%d file(s) byte-identical to the parent, %d changed "
                           "only in declared lines, %d new"
                           % (unchanged, len(declared), len(owned_files))))
    return out


# ------------------------------------------------------------------------------
# V10 -- file set, control file name and table prefix
#
# A renamed prefix silently degrades ercot7k_pso.py's preflight, which resolves
# the case by <prefix>.csv.
# ------------------------------------------------------------------------------
def _v10_file_set(ctx: _VerifyContext) -> List[Finding]:
    out: List[Finding] = []
    control = ctx.case_dir / ("%s.csv" % ctx.prefix)
    if ctx.prefix != DEFAULT_PREFIX:
        out.append(Finding("V10", LEVEL_ERROR,
                           "control file is %s.csv, expected %s.csv"
                           % (ctx.prefix, DEFAULT_PREFIX)))
    if not control.is_file():
        out.append(Finding("V10", LEVEL_ERROR,
                           "control file %s is missing" % control.name))
    stray = sorted(p.name for p in case_csv_files(ctx.case_dir)
                   if p.stem != ctx.prefix
                   and not p.stem.startswith(ctx.prefix + "_"))
    if stray:
        out.append(Finding("V10", LEVEL_ERROR,
                           "CSV file(s) not named %s_*: %s"
                           % (ctx.prefix, ", ".join(stray))))
    if ctx.manifest:
        parent_dir = Path(ctx.manifest["parent"]["path"])
        if parent_dir.is_dir():
            parent_names = {p.name for p in case_files(parent_dir)}
            here = {p.name for p in case_files(ctx.case_dir)}
            expected = parent_names | set(ctx.manifest.get("owned_files", []))
            extra = sorted(here - expected)
            missing = sorted(expected - here)
            if extra:
                out.append(Finding("V10", LEVEL_ERROR,
                                   "file(s) present here but neither inherited "
                                   "nor declared: %s" % ", ".join(extra)))
            if missing:
                out.append(Finding("V10", LEVEL_ERROR,
                                   "file(s) expected but absent: %s"
                                   % ", ".join(missing)))
    else:
        out.append(Finding("V10", LEVEL_SKIP,
                           "no manifest, so no parent file set to compare"))
    if not any(f.level == LEVEL_ERROR for f in out):
        out.append(Finding("V10", LEVEL_OK,
                           "control file %s.csv, %d table file(s), all %s_*"
                           % (ctx.prefix, len(case_csv_files(ctx.case_dir)) - 1,
                              ctx.prefix)))
    return out


# ------------------------------------------------------------------------------
# V11 -- capacity ceilings
#
# Every SCN_INJ_MAX.MaxMw must sit at or below the injector's INJ_ID.MaxMw, and
# byog_p_nom must equal byog_max_mw until SCN_INJ_MAX support lands. Otherwise
# a stress step is silently capped at the ceiling.
# ------------------------------------------------------------------------------
def _v11_capacity_ceiling(ctx: _VerifyContext) -> List[Finding]:
    out: List[Finding] = []
    nameplate = {r["Injector"]: _as_float(r.get("MaxMw", ""))
                 for r in ctx.rec("INJ_ID")}
    rows = [r for r in ctx.rec("SCN_INJ_MAX")
            if (r.get("MaxMw") or "").strip() != ""]
    over = ["%s %s > %s" % (r["Injector"], r["MaxMw"],
                            nameplate.get(r["Injector"]))
            for r in rows
            if _as_float(r["MaxMw"]) > nameplate.get(r["Injector"], 0.0)]
    if over:
        out.append(Finding("V11", LEVEL_ERROR,
                           "SCN_INJ_MAX.MaxMw above INJ_ID.MaxMw: %s"
                           % "; ".join(over[:10])))
    if not rows:
        out.append(Finding("V11", LEVEL_SKIP,
                           "no SCN_INJ_MAX row carries a MaxMw value"))

    checked = 0
    if ctx.manifest:
        for entry in (ctx.manifest.get("study") or {}).get("datacenters", []):
            checked += 1
            if float(entry.get("byog_p_nom_mw", 0.0)) != float(
                    entry.get("byog_max_mw", 0.0)):
                out.append(Finding("V11", LEVEL_ERROR,
                                   "%s byog_p_nom_mw != byog_max_mw and "
                                   "SCN_INJ_MAX support has not landed, so "
                                   "INJ_ID.MaxMw alone sets BYOG capacity"
                                   % entry.get("dc_name")))
            ceiling = (ctx.manifest.get("capacity_ceilings") or {}).get(
                entry.get("byog_injector"))
            plate = nameplate.get(entry.get("byog_injector"))
            if ceiling is not None and plate is not None and float(ceiling) != plate:
                out.append(Finding("V11", LEVEL_ERROR,
                                   "%s capacity ceiling %s does not match "
                                   "INJ_ID.MaxMw %s"
                                   % (entry.get("byog_injector"), ceiling, plate)))
    if checked and not any(f.level == LEVEL_ERROR for f in out):
        out.append(Finding("V11", LEVEL_OK,
                           "%d datacenter(s) have byog_p_nom == byog_max_mw == "
                           "INJ_ID.MaxMw" % checked))
    elif not checked:
        out.append(Finding("V11", LEVEL_SKIP,
                           "no manifest datacenter entry to check byog_p_nom "
                           "against byog_max_mw"))
    return out


# ------------------------------------------------------------------------------
# V12 -- no injector receiving a cost delta is also on a cost curve
#
# EnergyCost and cost-curve costs are ADDITIVE, so a "set" on a curve unit is
# not ignored, it is added on top: no error, no warning, a plausible wrong
# number. Wired before the mc_bus lever exists.
# ------------------------------------------------------------------------------
def _v12_cost_curve_overlap(ctx: _VerifyContext) -> List[Finding]:
    if not ctx.manifest:
        return [Finding("V12", LEVEL_SKIP,
                        "no manifest, so no declared cost deltas")]
    priced: List[str] = []
    for entry in ctx.manifest.get("owned", []):
        fields = entry.get("fields") or {}
        for name in ("EnergyCost", "CostAdder"):
            value = fields.get(name, "")
            if value and _as_float(value) != 0.0:
                priced.append(entry["key"][0])
    if not priced:
        return [Finding("V12", LEVEL_SKIP,
                        "no owned row carries a non-zero cost field")]
    curved = {r["Injector"] for r in ctx.rec("CYC_INJ_CCV")}
    clash = sorted(set(priced) & curved)
    if clash:
        return [Finding("V12", LEVEL_ERROR,
                        "injector(s) given a cost delta are also mapped to a "
                        "cost curve, and the two costs are additive: %s"
                        % ", ".join(clash))]
    return [Finding("V12", LEVEL_OK,
                    "%d cost-carrying injector(s), none on a cost curve"
                    % len(set(priced)))]


# ------------------------------------------------------------------------------
# V13 -- costs inside the case's incremental-cost range
#
# texas7k.csv sets MinimumIncrementalCost=-250 and MaximumIncrementalCost=5000.
# A byog_mc outside that range risks being silently clipped.
# ------------------------------------------------------------------------------
def _v13_cost_range(ctx: _VerifyContext) -> List[Finding]:
    options = {r["OptionName"]: r["OptionValue"]
               for r in ctx.rec(CONTROL_TABLE)}
    low = options.get("MinimumIncrementalCost")
    high = options.get("MaximumIncrementalCost")
    if low is None or high is None:
        return [Finding("V13", LEVEL_SKIP,
                        "control file sets no MinimumIncrementalCost / "
                        "MaximumIncrementalCost")]
    low_value, high_value = _as_float(low), _as_float(high)
    bad: List[str] = []
    for record in ctx.rec("INJ_ID"):
        cost = _as_float(record.get("EnergyCost", "")) + _as_float(
            record.get("CostAdder", ""))
        if cost < low_value or cost > high_value:
            bad.append("%s cost %s outside [%s, %s]"
                       % (record["Injector"], cost, low, high))
    if bad:
        return [Finding("V13", LEVEL_ERROR,
                        "injector cost(s) outside the case's incremental-cost "
                        "range and at risk of silent clipping: %s"
                        % "; ".join(bad[:10]))]
    return [Finding("V13", LEVEL_OK,
                    "all INJ_ID costs inside [%s, %s]" % (low, high))]


def _as_float(text: str) -> float:
    text = (text or "").strip()
    if text == "":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return float("nan")


# ------------------------------------------------------------------------------
# main()
#
# A thin argparse entry so the module can be exercised without the front end.
# The operator-facing script with the house-style banner, confirm() gate and
# Tee logging is ercot7k_build.py (Milestone 2); this is not it.
# ------------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ercot7k case delta writer (library entry point)")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="run V1-V13 on a case directory")
    verify.add_argument("case_dir")
    verify.add_argument("--strict-monitored", action="store_true",
                        help="promote the V8 unmonitored-node warning to an error")

    build = sub.add_parser("build", help="write one datacenter layer")
    build.add_argument("--base", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--dc-name", required=True)
    build.add_argument("--node", required=True)
    build.add_argument("--p-set-mw", type=float, required=True)
    build.add_argument("--byog-p-nom-mw", type=float, required=True)
    build.add_argument("--byog-max-mw", type=float, required=True)
    build.add_argument("--byog-mc", type=float, required=True)
    build.add_argument("--strict-monitored", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "verify":
        findings = verify_case(args.case_dir,
                               strict_monitored=args.strict_monitored)
        print(format_findings(findings))
        return 1 if has_errors(findings) else 0

    spec = DatacenterSpec(
        dc_name=args.dc_name, node=args.node, p_set_mw=args.p_set_mw,
        byog_p_nom_mw=args.byog_p_nom_mw, byog_max_mw=args.byog_max_mw,
        byog_mc=args.byog_mc,
    )
    manifest = build_datacenter_layer(args.base, args.out, spec,
                                      strict_monitored=args.strict_monitored)
    print("wrote layer %s to %s" % (manifest["layer_id"], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
