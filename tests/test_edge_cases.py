"""边界场景：单孔自身横贯、净距 0 下限、空输入、dict 写法机型。"""

import pytest

from app.models import Machine, Note, load_machine, load_notes
from app.solver import solve

from conftest import post_solve


SINGLE_SPAN_MACHINE = """
tick_length: 10
hole_width: 70
hole_height: 10
paper_width: 60
min_clearance: 8
bridge_width: 4
tracks:
  - {id: 1, x: 30}
"""

DICT_TRACK_MACHINE = """
tick_length: 10
hole_width: 26
hole_height: 11
paper_width: 60
min_clearance: 8
bridge_width: 2
tracks:
  1: 14
  2: 30
"""


def test_single_hole_spanning_both_edges_is_chain(client):
    csv_text = "id,track,tick,before,after,chord\n1,1,10,0,0,\n"
    body = post_solve(client, SINGLE_SPAN_MACHINE, csv_text).json()
    assert body["status"] == "infeasible_chain"
    assert body["weak_chain"]["note_ids"] == [1]
    h = body["weak_chain"]["holes"][0]["expanded_rect"]
    assert h["x0"] <= 0 and h["x1"] >= 60


def test_zero_min_clearance_requires_exact_touch(client):
    """min_clearance=0 时两孔可恰好相切（净距=0 合法），重叠（净距<0）非法。"""
    machine = Machine(
        tick_length=10, tracks={1: 30}, hole_width=10, hole_height=10,
        paper_width=60, min_clearance=0, bridge_width=0,
    )
    notes = [
        Note(1, 1, 10, 1, 1, None),
        Note(2, 1, 10, 1, 1, None),
    ]
    r = solve(machine, notes)
    # 同 tick 净距 -10 非法；Δt=1 净距 0 恰好合法
    assert r.feasible
    vec = sorted(r.displacements.items())
    assert vec == [(1, -1), (2, 0)] or vec == [(1, 0), (2, 1)]


def test_empty_csv_and_header_only_rejected(client):
    r1 = post_solve(client, SINGLE_SPAN_MACHINE, "\n")
    assert r1.status_code == 422
    r2 = post_solve(
        client, SINGLE_SPAN_MACHINE,
        "id,track,tick,before,after,chord\n",
    )
    assert r2.status_code == 422
    assert any("音符行" in e for e in r2.json()["errors"])


def test_mapping_style_tracks_accepted(client):
    body = post_solve(
        client, DICT_TRACK_MACHINE,
        "id,track,tick,before,after,chord\n1,1,10,0,0,\n2,2,10,0,0,\n",
    ).json()
    assert body["status"] in {"feasible", "infeasible_chain"}


def test_half_integer_geometry_serialized_as_number(client):
    """孔高 11 + 桥 2：y 边界出现 .5，必须用数值而非字符串表达。"""
    body = post_solve(
        client, DICT_TRACK_MACHINE,
        "id,track,tick,before,after,chord\n1,1,10,0,0,\n",
    ).json()
    assert body["status"] == "feasible"
    rect = body["holes"][0]["hole_rect"]
    assert rect["y0"] == 94.5 and rect["y1"] == 105.5
    exp = body["holes"][0]["expanded_rect"]
    assert exp["y0"] == 93.5 and exp["y1"] == 106.5
