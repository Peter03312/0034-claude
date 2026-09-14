"""场景二：临界净距。

孔高 12（奇数半高）、每 tick 10：|Δt|=2 时净距 = 20-12 = 8 恰好等于下限，
闭区间净距“不低于下限”必须判定合法（要求精确，差一个单位即非法）。
"""

from conftest import (
    MACHINE_CRITICAL,
    NOTES_CLEARANCE_CONFLICT,
    NOTES_CRITICAL,
    post_solve,
)


def test_clearance_exactly_at_limit_is_feasible(client):
    resp = post_solve(client, MACHINE_CRITICAL, NOTES_CRITICAL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "feasible", body
    # d=0 时 note2(tick8) 与 note3(tick10) 净距恰好 8，零位移即为最优
    assert body["displacements"] == {"1": 0, "2": 0, "3": 0}
    assert body["objective"] == {
        "max_abs_displacement": 0,
        "total_abs_displacement": 0,
    }


def test_one_unit_tighter_clearance_conflicts(client):
    """孔高 13 + note3 位于 tick9：note2 三种位移下净距分别为
    7(Δ1)/-3(Δ0)/7(Δ1)，全部 < 8，无组合通过净距，最小冲突对 (2,3)。"""
    tighter = MACHINE_CRITICAL.replace("hole_height: 12", "hole_height: 13")
    notes = (
        "id,track,tick,before,after,chord\n"
        "1,1,10,0,0,\n"
        "2,2,8,1,1,\n"
        "3,2,9,0,0,\n"
    )
    resp = post_solve(client, tighter, notes)
    body = resp.json()
    assert body["status"] == "infeasible_clearance"
    c = body["conflict"]
    assert (c["id_a"], c["id_b"]) == (2, 3)
    assert c["track"] == 2
    assert c["required_min_clearance"] == 8
    # d=0（Δt=1）时净距 -3，是编号对最小冲突的真实见证
    assert c["displacement_a"] == 0
    assert c["gap"] == -3


def test_inevitable_clearance_conflict_reports_min_id_pair(client):
    """note2 在 tick7..9 之间任意移动都与固定 tick7 的 note3 冲突，
    穷举所有组合都过不了净距：报告编号对最小的真实冲突 (2,3)。"""
    resp = post_solve(client, MACHINE_CRITICAL, NOTES_CLEARANCE_CONFLICT)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible_clearance", body
    c = body["conflict"]
    assert (c["id_a"], c["id_b"]) == (2, 3)
    assert c["track"] == 2
    assert c["gap"] < 8
    # 必须是穷举中真实出现的调整后 tick 与位移，不得是占位值
    assert c["adjusted_tick_a"] - c["adjusted_tick_b"] <= 1
    assert c["displacement_a"] in (-1, 0, 1)
    assert c["displacement_b"] == 0
    assert c["gap"] == (
        abs(c["adjusted_tick_a"] - c["adjusted_tick_b"]) * 10 - 12
    )
