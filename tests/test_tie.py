"""场景四：稳定并列。

d=+1 与 d=-1 的最大/总绝对位移完全相同，且都合法、d=0 因横贯非法。
确定性要求：反复求解结果一致，且选择按编号升序位移向量字典序更小的 -1。
"""

from conftest import MACHINE_TIE, NOTES_TIE, post_solve


def test_lexicographic_tie_breaks_to_negative(client):
    resp = post_solve(client, MACHINE_TIE, NOTES_TIE)
    body = resp.json()
    assert body["status"] == "feasible", body
    assert body["displacements"] == {"1": 0, "2": -1, "3": 0}
    assert body["objective"] == {
        "max_abs_displacement": 1,
        "total_abs_displacement": 1,
    }


def test_result_is_stable_across_repeated_requests(client):
    seen = set()
    for _ in range(5):
        body = post_solve(client, MACHINE_TIE, NOTES_TIE).json()
        seen.add(tuple(sorted(body["displacements"].items())))
    assert seen == {(("1", 0), ("2", -1), ("3", 0))}


def test_lex_order_is_by_note_id_not_group_order(client):
    """CSV 行序打乱后，最优判定仍按音符编号升序的位移向量。"""
    shuffled = """id,track,tick,before,after,chord
3,3,10,0,0,
1,1,10,0,0,
2,2,10,1,1,
"""
    body = post_solve(client, MACHINE_TIE, shuffled).json()
    assert body["status"] == "feasible"
    assert list(body["displacements"].items()) == [
        ("1", 0), ("2", -1), ("3", 0)
    ]
    # 孔序列也按编号升序返回
    assert [h["id"] for h in body["holes"]] == [1, 2, 3]
