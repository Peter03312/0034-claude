"""输入装配与孔区模型。

几何约定（题目以同一整数单位给尺寸，但安全桥宽外扩一半会产生半整数）：
所有内部坐标统一放大 2 倍，全程整数运算：

    孔矩形        x ∈ [2cx - w, 2cx + w],  y ∈ [2tick*L - h, 2tick*L + h]
    外扩矩形      在孔矩形四边上再外扩 b（安全桥宽，自身已是放大值 2*(b0/2)=b0）

其中 cx 为轨道中心横坐标，w = 孔模横宽，h = 孔模纵长，L = 每 tick 纵向长度。
闭区间“接触或重叠即连通”等价于两向投影均相交（闭区间，含端点）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import yaml


class InputError(ValueError):
    """输入装配错误：messages 收集所有错误，整单拒绝时一次性返回。"""

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("; ".join(messages))


# ---------------------------------------------------------------------------
# 机型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Machine:
    tick_length: int          # 每 tick 纵向长度
    tracks: dict[int, int]    # 轨道编号 -> 轨道中心横坐标
    hole_width: int           # 孔模横宽
    hole_height: int          # 孔模纵长
    paper_width: int          # 纸宽
    min_clearance: int        # 同轨最小净距
    bridge_width: int         # 安全桥宽

    # -- 放大 2 倍后的派生量（均为整数） --
    @property
    def s2_tick_length(self) -> int:
        return 2 * self.tick_length

    @property
    def s2_half_width(self) -> int:
        return self.hole_width

    @property
    def s2_half_height(self) -> int:
        return self.hole_height

    @property
    def s2_paper_width(self) -> int:
        return 2 * self.paper_width

    @property
    def s2_min_clearance(self) -> int:
        return 2 * self.min_clearance

    @property
    def s2_bridge(self) -> int:
        """每边外扩量（放大坐标下）：b0/2 * 2 = b0。"""
        return self.bridge_width

    def scaled_rect(self, track: int, adjusted_tick: int) -> tuple[int, int, int, int]:
        """返回该孔在放大坐标下的孔矩形 (x0, y0, x1, y1)（含端点闭区间）。"""
        cx = self.tracks[track]
        cy = adjusted_tick * self.s2_tick_length
        return (
            2 * cx - self.s2_half_width,
            cy - self.s2_half_height,
            2 * cx + self.s2_half_width,
            cy + self.s2_half_height,
        )

    def expanded_rect(self, track: int, adjusted_tick: int) -> tuple[int, int, int, int]:
        """孔矩形四边各外扩安全桥宽的一半（放大坐标下即外扩 bridge_width）。"""
        x0, y0, x1, y1 = self.scaled_rect(track, adjusted_tick)
        b = self.s2_bridge
        return x0 - b, y0 - b, x1 + b, y1 + b

    def same_track_gap(self, tick_a: int, tick_b: int) -> int:
        """同轨两孔纵向闭区间净距（原单位，可为负表示重叠）。

        孔纵长 H，中心在 t*L：区间 [tL - H/2, tL + H/2]。
        净距 = |Δt|*L - H。
        """
        return abs(tick_a - tick_b) * self.tick_length - self.hole_height

    def touches_left_edge(self, rect: tuple[int, int, int, int]) -> bool:
        return rect[0] <= 0

    def touches_right_edge(self, rect: tuple[int, int, int, int]) -> bool:
        return rect[2] >= self.s2_paper_width


def _as_int(value, name: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"机型参数 {name} 必须是整数，得到 {value!r}")
        return None
    return value


def load_machine(yaml_text: str) -> Machine:
    """解析并校验机型 YAML。所有错误聚齐后一次性抛出。"""
    errors: list[str] = []
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise InputError([f"机型 YAML 解析失败: {exc}"]) from exc
    if not isinstance(data, dict):
        raise InputError(["机型 YAML 顶层必须是映射"])

    def need(key: str):
        if key not in data:
            errors.append(f"机型 YAML 缺少必填字段 {key!r}")
            return None
        return data[key]

    tick_length = _as_int(need("tick_length"), "tick_length", errors)
    hole_width = _as_int(need("hole_width"), "hole_width", errors)
    hole_height = _as_int(need("hole_height"), "hole_height", errors)
    paper_width = _as_int(need("paper_width"), "paper_width", errors)
    min_clearance = _as_int(need("min_clearance"), "min_clearance", errors)
    bridge_width = _as_int(need("bridge_width"), "bridge_width", errors)

    tracks: dict[int, int] = {}
    raw_tracks = need("tracks")
    if isinstance(raw_tracks, list):
        for item in raw_tracks:
            if isinstance(item, dict) and {"id", "x"} <= item.keys():
                tid, tx = item["id"], item["x"]
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                tid, tx = item
            else:
                errors.append(f"轨道条目必须是 {{id, x}} 或 [id, x]，得到 {item!r}")
                continue
            if isinstance(tid, bool) or not isinstance(tid, int):
                errors.append(f"轨道编号必须是整数，得到 {tid!r}")
                continue
            if isinstance(tx, bool) or not isinstance(tx, int):
                errors.append(f"轨道 {tid} 中心横坐标必须是整数，得到 {tx!r}")
                continue
            if tid in tracks:
                errors.append(f"轨道编号 {tid} 重复")
                continue
            tracks[tid] = tx
    elif isinstance(raw_tracks, dict):
        for key, tx in raw_tracks.items():
            if not isinstance(key, int) or isinstance(key, bool):
                # 不接受 "1.5" 这类被 int() 静默截断的写法
                try:
                    tid = int(str(key), 10)
                    if str(tid) != str(key).strip():
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(f"轨道编号必须是整数，得到 {key!r}")
                    continue
            else:
                tid = key
            if isinstance(tx, bool) or not isinstance(tx, int):
                errors.append(f"轨道 {tid} 中心横坐标必须是整数，得到 {tx!r}")
                continue
            tracks[tid] = tx
    else:
        if raw_tracks is not None:
            errors.append("tracks 必须是列表或映射")

    # 语义校验（与结构错误聚齐后一次性返回，不提前中断）
    if not tracks:
        errors.append("至少要定义一条轨道")
    if tick_length is not None and tick_length <= 0:
        errors.append("tick_length 必须为正数")
    if hole_width is not None and hole_width <= 0:
        errors.append("hole_width 必须为正数")
    if hole_height is not None and hole_height <= 0:
        errors.append("hole_height 必须为正数")
    if paper_width is not None and paper_width <= 0:
        errors.append("paper_width 必须为正数")
    if min_clearance is not None and min_clearance < 0:
        errors.append("min_clearance 不得为负")
    if bridge_width is not None and bridge_width < 0:
        errors.append("bridge_width 不得为负")
    if paper_width is not None:
        for tid, cx in tracks.items():
            if cx < 0 or cx > paper_width:
                errors.append(f"轨道 {tid} 中心横坐标 {cx} 超出纸宽 [0, {paper_width}]")
    if errors:
        raise InputError(errors)

    return Machine(
        tick_length=tick_length,
        tracks=dict(sorted(tracks.items())),
        hole_width=hole_width,
        hole_height=hole_height,
        paper_width=paper_width,
        min_clearance=min_clearance,
        bridge_width=bridge_width,
    )


# ---------------------------------------------------------------------------
# 逐音符 CSV
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ("id", "track", "tick", "before", "after", "chord")


@dataclass
class Note:
    note_id: int
    track: int
    tick: int
    before: int
    after: int
    chord: str | None
    # 求解器填充
    group_index: int = -1
    displacement: int = 0

    @property
    def adjusted_tick(self) -> int:
        return self.tick + self.displacement


def load_notes(csv_text: str, machine: Machine) -> list[Note]:
    """解析逐音符 CSV 并对照机型校验，错误聚齐后整单拒绝。"""
    errors: list[str] = []
    notes: list[Note] = []
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise InputError(["CSV 为空或缺少表头"])
    header = [h.strip() for h in reader.fieldnames]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise InputError([f"CSV 表头缺少列: {', '.join(missing)}"])

    seen_ids: set[int] = set()

    def row_int(row: dict[str, str], key: str, lineno: int) -> int | None:
        raw = (row.get(key) or "").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors.append(f"CSV 第 {lineno} 行 {key}={raw!r} 不是整数")
            return None
        return value

    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # 跳过空行
        note_id = row_int(row, "id", i)
        track = row_int(row, "track", i)
        tick = row_int(row, "tick", i)
        before = row_int(row, "before", i)
        after = row_int(row, "after", i)
        chord_raw = (row.get("chord") or "").strip()
        chord = chord_raw if chord_raw else None

        if note_id is not None and note_id in seen_ids:
            errors.append(f"CSV 第 {i} 行编号 {note_id} 重复")
        if note_id is not None:
            seen_ids.add(note_id)
        if before is not None and before < 0:
            errors.append(f"CSV 第 {i} 行 before 不得为负")
        if after is not None and after < 0:
            errors.append(f"CSV 第 {i} 行 after 不得为负")
        if track is not None and track not in machine.tracks:
            errors.append(f"CSV 第 {i} 行轨道 {track} 未在机型中定义")
        if None in (note_id, track, tick, before, after):
            continue
        notes.append(Note(note_id, track, tick, before, after, chord))  # type: ignore[arg-type]

    if not notes and not errors:
        errors.append("CSV 没有任何音符行")

    if errors:
        raise InputError(errors)
    return notes
