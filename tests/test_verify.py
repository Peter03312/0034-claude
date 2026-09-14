"""POST /verify：候选位移表复核（只判定，不重新搜索）。

覆盖：
* /solve 结果原样复核（feasible 与 chord 组）；
* 同轨净距冲突取证（unsafe_clearance 最小编号对）；
* 外扩矩形横贯链取证（unsafe_chain 字典序最小真实链）；
* 候选字段错误（缺号、多号、非整数、越界、同和弦不一致、JSON 结构）
  按编号稳定汇总为 422；
* 机型/音符原有聚合校验同样适用于 /verify；
* 同一输入重复复核结果一致。
"""

import json

from conftest import (
    MACHINE_CHAIN,
    MACHINE_CRITICAL,
    MACHINE_GREEDY,
    MACHINE_TIE,
    NOTES_CHAIN,
    NOTES_CLEARANCE_CONFLICT,
    NOTES_GREEDY,
    NOTES_TIE,
    post_solve,
    post_verify,
)


# ---------------------------------------------------------------------------
# 求解结果原样复核
# ---------------------------------------------------------------------------

def test_solve_result_roundtrip_verifies(client):
    solve_body = post_solve(client, MACHINE_GREEDY, NOTES_GREEDY).json()
    assert solve_body["status"] == "feasible"

    resp = post_verify(
        client, MACHINE_GREEDY, NOTES_GREEDY, solve_body["displacements"]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "verified"
    # 复核值、目标值与 /solve 完全一致（不重新搜索，只判定候选）
    assert body["displacements"] == solve_body["displacements"]
    assert body["objective"] == solve_body["objective"]
    assert body["chord_displacements"] == solve_body["chord_displacements"]
    assert len(body["holes"]) == 4

    by_id = {h["id"]: h for h in body["holes"]}
    for h in body["holes"]:
        solve_h = next(x for x in solve_body["holes"] if x["id"] == h["id"])
        assert h["adjusted_tick"] == solve_h["adjusted_tick"]
        assert h["center"] == solve_h["center"]
        assert h["hole_rect"] == solve_h["hole_rect"]
        assert h["expanded_rect"] == solve_h["expanded_rect"]
    # 逐孔几何与机型约定：孔 2/4 同和弦，tick 12；孔 3 tick 8
    assert by_id[2]["adjusted_tick"] == 12
    assert by_id[4]["adjusted_tick"] == 12


def test_zero_vector_tie_machine_roundtrip(client):
    solve_body = post_solve(client, MACHINE_TIE, NOTES_TIE).json()
    body = post_verify(
        client, MACHINE_TIE, NOTES_TIE, solve_body["displacements"]
    ).json()
    assert body["status"] == "verified"
    assert body["displacements"] == {"1": 0, "2": -1, "3": 0}
    assert body["objective"] == {
        "max_abs_displacement": 1,
        "total_abs_displacement": 1,
    }


# ---------------------------------------------------------------------------
# 净距冲突取证
# ---------------------------------------------------------------------------

def test_unsafe_clearance_reports_min_id_pair(client):
    resp = post_verify(
        client,
        MACHINE_CRITICAL,
        NOTES_CLEARANCE_CONFLICT,
        {"1": 0, "2": 0, "3": 0, "4": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsafe_clearance"
    c = body["conflict"]
    # 与 /solve 在该实例上的必然冲突一致：编号对最小的真实冲突 (2,3)
    assert (c["id_a"], c["id_b"]) == (2, 3)
    assert c["track"] == 2
    assert c["required_min_clearance"] == 8
    assert c["gap"] < 8
    # 必须是候选值上的真实见证，不是占位值
    assert c["gap"] == (
        abs(c["adjusted_tick_a"] - c["adjusted_tick_b"]) * 10 - 12
    )
    assert c["displacement_a"] == 0 and c["displacement_b"] == 0


def test_clearance_conflict_pair_is_smallest_even_with_earlier_notes(client):
    """编号 1/4 固定合法，冲突只发生在 2/3：最小编号对不得被更大的对遮蔽。"""
    # MACHINE_CRITICAL 孔高 12：note1(t10) 与 note2(t8) 净距 8 恰好合法，
    # note2(t8) 与 note3(t7) 净距 -2 冲突，note2 与 note4(t10) 恰好合法。
    body = post_verify(
        client,
        MACHINE_CRITICAL,
        NOTES_CLEARANCE_CONFLICT,
        {"1": 0, "2": 0, "3": 0, "4": 0},
    ).json()
    assert body["status"] == "unsafe_clearance"
    assert (body["conflict"]["id_a"], body["conflict"]["id_b"]) == (2, 3)


# ---------------------------------------------------------------------------
# 横贯链取证
# ---------------------------------------------------------------------------

def test_unsafe_chain_reports_real_path_fixed_instance(client):
    body = post_verify(
        client, MACHINE_CHAIN, NOTES_CHAIN, {"1": 0, "2": 0, "3": 0}
    ).json()
    assert body["status"] == "unsafe_chain"
    assert body["weak_chain"]["note_ids"] == [1, 2, 3]
    holes = {h["id"]: h for h in body["weak_chain"]["holes"]}

    first = holes[body["weak_chain"]["note_ids"][0]]
    last = holes[body["weak_chain"]["note_ids"][-1]]
    assert first["expanded_rect"]["x0"] <= 0
    assert last["expanded_rect"]["x1"] >= 60
    ids = body["weak_chain"]["note_ids"]
    for a, b in zip(ids, ids[1:]):
        ra, rb = holes[a]["expanded_rect"], holes[b]["expanded_rect"]
        assert ra["x0"] <= rb["x1"] and rb["x0"] <= ra["x1"]
        assert ra["y0"] <= rb["y1"] and rb["y0"] <= ra["y1"]


def test_unsafe_chain_on_feasible_instances_zero_vector(client):
    """/solve 的可行实例（贪心机型）上，零位移候选被判 unsafe_chain，

    取证链为字典序最小的真实链 [1,2,4]：复核只判候选值，不重新搜索。
    """
    body = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 0, "2": 0, "3": 0, "4": 0},
    ).json()
    assert body["status"] == "unsafe_chain"
    ids = body["weak_chain"]["note_ids"]
    assert ids == [1, 2, 4]
    holes = {h["id"]: h for h in body["weak_chain"]["holes"]}
    assert holes[ids[0]]["expanded_rect"]["x0"] <= 0
    assert holes[ids[-1]]["expanded_rect"]["x1"] >= 60
    for a, b in zip(ids, ids[1:]):
        ra, rb = holes[a]["expanded_rect"], holes[b]["expanded_rect"]
        assert ra["x0"] <= rb["x1"] and rb["x0"] <= ra["x1"]
        assert ra["y0"] <= rb["y1"] and rb["y0"] <= ra["y1"]


# ---------------------------------------------------------------------------
# 候选字段错误
# ---------------------------------------------------------------------------

def test_missing_id_rejected(client):
    resp = post_verify(
        client, MACHINE_GREEDY, NOTES_GREEDY, {"1": 0, "2": 2, "4": 2}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "invalid_input"
    assert "复核" in body["message"]
    assert any("缺少编号 3" in e for e in body["errors"])


def test_extra_id_rejected(client):
    resp = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 0, "2": 2, "3": 0, "4": 2, "9": 0},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("没有的编号" in e and "9" in e for e in errors)


def test_non_integer_displacement_rejected(client):
    # 2.0 是 JSON 浮点：不得静默取整；字符串同样拒绝
    resp = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 0, "2": 2.0, "3": "0", "4": 2},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("编号 2" in e and "整数" in e for e in errors)
    assert any("编号 3" in e and "整数" in e for e in errors)


def test_out_of_domain_displacement_rejected(client):
    # 编号 2 允许域 [-2,2]；编号 1 固定为 0
    resp = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 5, "2": 2, "3": 0, "4": 2},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("编号 1" in e and "超出允许域" in e and "0" in e for e in errors)
    # 越界候选不得误判成 unsafe_*：没有任何判定结果
    assert all(k not in resp.json() for k in ("conflict", "weak_chain"))


def test_chord_members_inconsistent_rejected(client):
    # 编号 2、4 同属和弦 C1，候选值不一致：结构非法，不进入几何判定
    resp = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 0, "2": 2, "3": 0, "4": 0},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("和弦" in e and "不一致" in e for e in errors), errors


def test_all_field_errors_aggregated_in_stable_id_order(client):
    """缺号 + 非整数 + 越界 + 多号一次性聚齐，顺序按编号稳定汇总。"""
    cand = {"1": 9, "2": "x", "4": 2, "7": 1}
    resp = post_verify(client, MACHINE_GREEDY, NOTES_GREEDY, cand)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    # 真实编号的错误在前（1 越界, 2 非整数, 3 缺号），多号 7 在最后
    assert len(errors) == 4
    assert "编号 1" in errors[0] and "超出允许域" in errors[0]
    assert "编号 2" in errors[1] and "整数" in errors[1]
    assert "缺少编号 3" in errors[2]
    assert "没有的编号" in errors[3] and "7" in errors[3]


def test_duplicate_json_key_rejected(client):
    raw = '{"1": 0, "2": 2, "3": 0, "4": 2, "1": 2}'
    resp = post_verify(client, MACHINE_GREEDY, NOTES_GREEDY, raw)
    assert resp.status_code == 422
    assert any("重复键" in e for e in resp.json()["errors"])


def test_malformed_json_rejected(client):
    resp = post_verify(client, MACHINE_GREEDY, NOTES_GREEDY, "{not json")
    assert resp.status_code == 422
    assert any("JSON" in e for e in resp.json()["errors"])


def test_non_object_json_rejected(client):
    resp = post_verify(client, MACHINE_GREEDY, NOTES_GREEDY, "[1, 2, 3]")
    assert resp.status_code == 422
    assert any("映射" in e for e in resp.json()["errors"])


def test_missing_displacements_field_rejected(client):
    resp = client.post(
        "/verify",
        files={
            "machine": ("machine.yaml", MACHINE_GREEDY, "application/x-yaml"),
            "notes": ("notes.csv", NOTES_GREEDY, "text/csv"),
        },
    )
    assert resp.status_code == 422
    assert any("displacements" in e for e in resp.json()["errors"])


def test_non_integer_like_key_treated_as_extra(client):
    # "1.0" 不得被静默解释成编号 1
    resp = post_verify(
        client,
        MACHINE_GREEDY,
        NOTES_GREEDY,
        {"1": 0, "2": 2, "3": 0, "4": 2, "1.0": 1},
    )
    assert resp.status_code == 422
    assert any("没有的编号" in e for e in resp.json()["errors"])


# ---------------------------------------------------------------------------
# 机型 / 音符原有聚合校验仍适用
# ---------------------------------------------------------------------------

def test_bad_machine_still_rejected_at_verify(client):
    bad = MACHINE_GREEDY.replace("tick_length: 10", "tick_length: 0")
    resp = post_verify(
        client, bad, NOTES_GREEDY, {"1": 0, "2": 2, "3": 0, "4": 2}
    )
    assert resp.status_code == 422
    assert any("tick_length" in e for e in resp.json()["errors"])


def test_bad_notes_still_rejected_at_verify(client):
    bad = """id,track,tick,before,after,chord
1,9,10,0,0,
2,2,10,0,0,
"""
    resp = post_verify(client, MACHINE_GREEDY, bad, {"1": 0, "2": 0})
    assert resp.status_code == 422
    joined = " ".join(resp.json()["errors"])
    assert "轨道" in joined


# ---------------------------------------------------------------------------
# 确定性：同一输入重复复核结果一致
# ---------------------------------------------------------------------------

def test_verified_result_stable_across_repeats(client):
    solve_body = post_solve(client, MACHINE_GREEDY, NOTES_GREEDY).json()
    seen = []
    for _ in range(5):
        body = post_verify(
            client, MACHINE_GREEDY, NOTES_GREEDY, solve_body["displacements"]
        ).json()
        seen.append(json.dumps(body, sort_keys=True))
    assert len(set(seen)) == 1


def test_unsafe_results_stable_across_repeats(client):
    seen_clearance, seen_chain = set(), set()
    for _ in range(3):
        b1 = post_verify(
            client,
            MACHINE_CRITICAL,
            NOTES_CLEARANCE_CONFLICT,
            {"1": 0, "2": 0, "3": 0, "4": 0},
        ).json()
        seen_clearance.add(json.dumps(b1, sort_keys=True))
        b2 = post_verify(
            client, MACHINE_CHAIN, NOTES_CHAIN, {"1": 0, "2": 0, "3": 0}
        ).json()
        seen_chain.add(json.dumps(b2, sort_keys=True))
    assert seen_clearance and len(seen_clearance) == 1
    assert seen_chain and len(seen_chain) == 1


def test_invalid_input_stable_across_repeats(client):
    seen = set()
    for _ in range(3):
        r = post_verify(
            client, MACHINE_GREEDY, NOTES_GREEDY, {"1": 9, "4": 2, "7": 0}
        )
        seen.add(json.dumps(r.json(), sort_keys=True))
    assert len(seen) == 1
