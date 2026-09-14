"""场景三：必然横贯。

三孔分别在三条轨道、全部固定同一 tick；孔宽 32 使相邻轨外扩矩形 x 投影重叠，
纵对齐后 左-中-右 搭接成同时触两边的分量。唯一组合过净距却必非法，
必须从该目标序最小向量返回孔编号序列字典序最小的真实贯通链。
"""

from conftest import MACHINE_CHAIN, NOTES_CHAIN, post_solve


def test_inevitable_chain_reported(client):
    resp = post_solve(client, MACHINE_CHAIN, NOTES_CHAIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible_chain", body
    chain = body["weak_chain"]["note_ids"]
    assert chain == [1, 2, 3]
    assert body["displacements"] == {"1": 0, "2": 0, "3": 0}
    assert body["objective"]["max_abs_displacement"] == 0
    assert body["objective"]["total_abs_displacement"] == 0


def test_chain_is_a_real_connecting_path(client):
    """贯通链必须是真实连通路径：相邻孔外扩矩形闭区间接触，
    首孔触左边、末孔触右边，不得返回凭空编号。"""
    resp = post_solve(client, MACHINE_CHAIN, NOTES_CHAIN)
    body = resp.json()
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
