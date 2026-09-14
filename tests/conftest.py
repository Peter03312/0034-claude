"""pytest 共享夹具：测试客户端与四类典型输入。

数值均经过手算复核，全部为整数单位（安全桥外扩一半产生的半整数在内部
放大 2 倍处理）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# 机型
# ---------------------------------------------------------------------------

# 三轨；孔宽 26 + 桥 2（每边外扩 1）：轨道 1 恰好触左边，轨道 3 恰好触右边，
# 相邻轨外扩矩形 x 间隙 = 32-28-2 = 2，不搭接；只有纵向上错开才能形成链。
MACHINE_GREEDY = """
tick_length: 10
hole_width: 26
hole_height: 11
paper_width: 60
min_clearance: 8
bridge_width: 2
tracks:
  - {id: 1, x: 14}
  - {id: 2, x: 30}
  - {id: 3, x: 46}
"""

# 两轨、奇数孔高 12：|Δt|=2 时净距恰好为 8（临界）
MACHINE_CRITICAL = """
tick_length: 10
hole_width: 26
hole_height: 12
paper_width: 60
min_clearance: 8
bridge_width: 0
tracks:
  - {id: 1, x: 14}
  - {id: 2, x: 30}
"""

# 三轨；孔宽 32 无桥宽：三轨孔两两 x 重叠，同 tick 必横贯
MACHINE_CHAIN = """
tick_length: 10
hole_width: 32
hole_height: 15
paper_width: 60
min_clearance: 8
bridge_width: 0
tracks:
  - {id: 1, x: 14}
  - {id: 2, x: 30}
  - {id: 3, x: 46}
"""

# 三轨；纸宽 62 / 孔宽 30：相邻轨 x 间隙 2，d=0 纵重叠形成对角链，
# 错开 ±1 后纵向间隙 4>桥 0，链断开；±1 完全并列
MACHINE_TIE = """
tick_length: 10
hole_width: 30
hole_height: 8
paper_width: 62
min_clearance: 8
bridge_width: 0
tracks:
  - {id: 1, x: 15}
  - {id: 2, x: 31}
  - {id: 3, x: 47}
"""


# ---------------------------------------------------------------------------
# 音符表
# ---------------------------------------------------------------------------

# 贪心取最近（0/-1/+1）必死：
#   C1 组（音符 2、4 共享位移，域 [-2,2]）
#   d=0：  note2 与 note3 同轨净距 9>=8 过净距，但三轨在 tick10 纵对齐，
#          1-2-4 外扩矩形形成左右横贯链；
#   d=-1： note2@9 与 note3@8 净距 9 过净距，纵向仍与 1@10、4@9 搭接，链贯通；
#   d=+1： note2@11 与 note3@8 净距 19 过净距，1-2 纵向间隙 1 仍接触，链贯通；
#   d=-2： note2 与 note3 同 tick8，净距 -11 < 8，净距剪枝；
#   d=+2： 净距 29，纵向链全部错开 —— 全局有解（贪心失败）。
NOTES_GREEDY = """id,track,tick,before,after,chord
1,1,10,0,0,
2,2,10,2,2,C1
4,3,10,2,2,C1
3,2,8,0,0,
"""

# note2 可在 tick 7..9；tick8 与 note3(tick10) 净距恰好 8 —— 临界合法
NOTES_CRITICAL = """id,track,tick,before,after,chord
1,1,10,0,0,
2,2,8,1,1,
3,2,10,0,0,
"""

# note2(8±1) 与 note3 固定 tick7：任意位移下净距 <=2 < 8，必然冲突，
# 最小冲突编号对为 (2,3)
NOTES_CLEARANCE_CONFLICT = """id,track,tick,before,after,chord
1,1,10,0,0,
2,2,8,1,1,
3,2,7,0,0,
4,2,10,0,0,
"""

# 三孔全部固定同 tick，外扩矩形两两搭接且分别触左右边 —— 必然贯通
NOTES_CHAIN = """id,track,tick,before,after,chord
1,1,10,0,0,
2,2,10,0,0,
3,3,10,0,0,
"""

# 中间孔可 ±1；d=0 成链，d=±1 均断开且目标值并列，应取字典序小的 -1
NOTES_TIE = """id,track,tick,before,after,chord
1,1,10,0,0,
2,2,10,1,1,
3,3,10,0,0,
"""


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def post_solve(client, yaml_text: str, csv_text: str):
    return client.post(
        "/solve",
        files={
            "machine": ("machine.yaml", yaml_text, "application/x-yaml"),
            "notes": ("notes.csv", csv_text, "text/csv"),
        },
    )


def post_verify(client, yaml_text: str, csv_text: str, displacements):
    """POST /verify；displacements 为 dict（内部 json.dumps）或原始字符串。"""
    import json

    raw = (
        displacements
        if isinstance(displacements, str)
        else json.dumps(displacements)
    )
    return client.post(
        "/verify",
        files={
            "machine": ("machine.yaml", yaml_text, "application/x-yaml"),
            "notes": ("notes.csv", csv_text, "text/csv"),
        },
        data={"displacements": raw},
    )
