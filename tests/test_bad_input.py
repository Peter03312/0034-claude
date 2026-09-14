"""场景五：坏输入整单拒绝。

无论 YAML 还是 CSV 出错，整单不执行求解，HTTP 422 返回结构化错误列表；
多个错误要一次性聚齐（不是遇到第一个就拒绝）。
"""

from conftest import (
    MACHINE_CHAIN,
    MACHINE_GREEDY,
    NOTES_GREEDY,
    post_solve,
)


def test_malformed_yaml_rejected(client):
    resp = post_solve(client, "tick_length: [1, 2\n", NOTES_GREEDY)
    assert resp.status_code == 422
    body = resp.json()
    assert body["status"] == "invalid_input"
    assert body["errors"]


def test_missing_yaml_fields_aggregated(client):
    bad = """
tick_length: 10
hole_width: 26
tracks: []
"""
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    errors = " ".join(resp.json()["errors"])
    # 缺字段与空轨道一次性聚齐
    assert "hole_height" in errors
    assert "paper_width" in errors
    assert "min_clearance" in errors
    assert "bridge_width" in errors
    assert "轨道" in errors


def test_non_mapping_yaml_rejected(client):
    resp = post_solve(client, "- just\n- a list\n", NOTES_GREEDY)
    assert resp.status_code == 422
    assert "映射" in " ".join(resp.json()["errors"])


def test_unknown_track_and_duplicate_ids_rejected(client):
    bad = """id,track,tick,before,after,chord
1,9,10,0,0,
1,1,10,0,0,
"""
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    joined = " ".join(errors)
    assert "9" in joined and "轨道" in joined
    assert any("重复" in e for e in errors)


def test_non_integer_and_negative_microadjust_rejected(client):
    bad = """id,track,tick,before,after,chord
1,1,x,0,0,
2,2,10,-1,0,
3,1,10,0,-2,
"""
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert len(errors) >= 3  # tick 非整数 + before 负 + after 负，整单聚齐


def test_missing_csv_column_rejected(client):
    bad = "id,track,tick,before,chord\n1,1,10,0,\n"
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    assert "after" in " ".join(resp.json()["errors"])


def test_non_positive_dimension_rejected(client):
    bad = MACHINE_GREEDY.replace("tick_length: 10", "tick_length: 0")
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    assert any("tick_length" in e for e in resp.json()["errors"])


def test_track_center_outside_paper_rejected(client):
    bad = MACHINE_GREEDY.replace("x: 46", "x: 99")
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    assert any("纸宽" in e for e in resp.json()["errors"])


def test_duplicate_track_id_rejected(client):
    bad = """
tick_length: 10
hole_width: 26
hole_height: 11
paper_width: 60
min_clearance: 8
bridge_width: 2
tracks:
  - {id: 1, x: 14}
  - {id: 1, x: 30}
"""
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    assert any("重复" in e for e in resp.json()["errors"])


def test_duplicate_track_id_in_mapping_rejected(client):
    """tracks 用 YAML 映射写法时同一轨道写两次：不得静默采用后一个位置。

    PyYAML 默认以后值覆盖前值，必须用严格加载器在解析期拒绝整单。
    """
    bad = """
tick_length: 10
hole_width: 26
hole_height: 11
paper_width: 60
min_clearance: 8
bridge_width: 2
tracks:
  1: 14
  1: 30
  2: 30
  3: 46
"""
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    joined = " ".join(resp.json()["errors"])
    assert "YAML" in joined and "重复键" in joined


def test_duplicate_key_at_nested_level_rejected(client):
    bad = "tick_length: 10\nmeta:\n  a: 1\n  a: 2\n"
    resp = post_solve(client, bad, NOTES_GREEDY)
    assert resp.status_code == 422
    assert any("重复键" in e for e in resp.json()["errors"])


def test_csv_row_with_more_columns_than_header_rejected(client):
    """超列曾经导致 500（DictReader 把多余值放进 None 键的列表）或静默吞列。"""
    # 多余列非空：明确报错
    bad = (
        "id,track,tick,before,after,chord\n"
        "1,1,10,0,0,,EXTRA\n"
        "2,2,10,0,0,\n"
    )
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("7 列" in e and "超过" in e for e in errors), errors

    # 全部多余列为空时也绝不 500（正是此前触发内部错误的形态）
    bad_blank = (
        "id,track,tick,before,after,chord\n"
        ",,,,,,\n"
        "1,1,10,0,0,\n"
    )
    resp2 = post_solve(client, MACHINE_GREEDY, bad_blank)
    assert resp2.status_code == 422


def test_csv_row_with_fewer_columns_than_header_rejected(client):
    bad = (
        "id,track,tick,before,after,chord\n"
        "1,1,10,0,0\n"          # 少 chord 列
        "2,2,10,0,0,\n"
    )
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    assert any("少于" in e for e in resp.json()["errors"])


def test_csv_duplicate_header_rejected(client):
    bad = "id,track,tick,before,after,id,chord\n1,1,10,0,0,1,\n"
    resp = post_solve(client, MACHINE_GREEDY, bad)
    assert resp.status_code == 422
    assert any("重复列" in e for e in resp.json()["errors"])


def test_good_input_with_other_machine_not_rejected(client):
    resp = post_solve(client, MACHINE_CHAIN, NOTES_GREEDY)
    # 换机型不是坏输入：正常进入求解（绝不 422）
    assert resp.status_code == 200
