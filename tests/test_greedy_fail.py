"""场景一：逐音符取最近时刻（贪心取 0，再 -1/+1）全部失败，
全局完整搜索在 +2 处得到唯一合法解；同时覆盖和弦组共享位移。"""

from conftest import (
    MACHINE_GREEDY,
    NOTES_GREEDY,
    post_solve,
)


def test_greedy_fails_but_global_solution_exists(client):
    resp = post_solve(client, MACHINE_GREEDY, NOTES_GREEDY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "feasible"

    # 最近时刻 0 不可能合法（断言最优必须移动到 |d|=2）
    assert body["objective"]["max_abs_displacement"] == 2

    disp = body["displacements"]
    # +2 是唯一可行方向（-2 与固定孔 note3 同轨重叠）
    assert disp == {"1": 0, "2": 2, "3": 0, "4": 2}
    assert body["objective"]["total_abs_displacement"] == 4

    # 和弦组共享同一个整数位移（音符 2、4）
    assert body["chord_displacements"] == {"C1": 2}

    by_id = {h["id"]: h for h in body["holes"]}
    # 孔中心 =（轨道横坐标，调整后 tick × 每 tick 长度）
    assert by_id[2]["center"] == {"x": 30, "y": 120}
    assert by_id[4]["center"] == {"x": 46, "y": 120}
    assert by_id[1]["center"] == {"x": 14, "y": 100}

    # 外扩矩形：四边各外扩安全桥宽的一半 = 1
    r1 = by_id[1]["expanded_rect"]
    assert r1 == {"x0": 0, "y0": 100 - 5.5 - 1,
                  "x1": 28, "y1": 100 + 5.5 + 1}

    # 同轨净距实测：note2(tick12) 与 note3(tick8)
    assert by_id[2]["adjusted_tick"] == 12
    assert by_id[3]["adjusted_tick"] == 8
    # 4 * 10 - 11 = 29 >= 8
    assert by_id[2]["adjusted_tick"] - by_id[3]["adjusted_tick"] == 4


def test_zero_displacement_actually_forms_chain(client):
    """反证：在 0 位移处，C1 组落孔过程中就形成左右贯通，逐音符贪心看不到这点。"""
    from app.models import load_machine, load_notes
    from app.solver import Solver, LEFT, RIGHT

    m = load_machine(MACHINE_GREEDY)
    notes = load_notes(NOTES_GREEDY, m)

    # 贪心会依次尝试的最近位移 0、-1、+1 全部被横贯剪枝
    for greedy_d in (0, -1, 1):
        s = Solver(m, notes)
        assert s._place(0, 0)[0] is True
        assert s._place(1, greedy_d)[0] is False

    # 全局解 +2：C1 组与后续固定组均合法
    s = Solver(m, notes)
    assert s._place(0, 0)[0] is True
    assert s._place(1, 2)[0] is True
    assert s._place(2, 0)[0] is True
    assert s.dsu.same(LEFT, RIGHT) is False

    # 失败的落孔会完整回滚并查集，不残留横贯状态
    s2 = Solver(m, notes)
    s2._place(0, 0)
    state = s2.dsu.checkpoint()
    s2._place(1, 0)
    s2.dsu.rollback(state)
    assert s2.dsu.same(LEFT, RIGHT) is False
