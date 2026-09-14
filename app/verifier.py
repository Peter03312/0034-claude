"""候选位移表复核（只判定，不重新搜索）。

输入：机型、已通过原聚合校验的音符清单，以及与 POST /solve 返回的
``displacements`` 同构的候选 JSON：``{音符编号字符串: 整数位移}``。

候选字段错误（缺号、多号、非整数、越界、同和弦不一致、JSON 结构错误）
按编号稳定聚齐后抛 :class:`InputError`（422），不执行任何安全判定——
避免把不完整候选误判成不可打孔。

判定从求解器的组模型与放大 2 倍整数几何抽出公共判定，依次执行：

1. **同轨净距**：同一轨道全部孔对（未外扩矩形）的纵向闭区间净距不得低于
   ``min_clearance``；不通过时返回编号对最小的实际冲突；
2. **外扩矩形横贯**：外扩矩形闭区间接触/重叠即连通，任一分量同时抵达
   左右纸边即非法；返回孔编号序列字典序最小的真实贯通链。

整个过程不枚举任何替代位移，复核结论只取决于给定候选值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .models import InputError, Machine, Note
from .solver import Group, build_groups, find_smallest_chain


# ---------------------------------------------------------------------------
# 候选 JSON 解析与字段校验
# ---------------------------------------------------------------------------

class _DuplicateKeyError(ValueError):
    """候选 JSON 映射存在重复键（携带该键）。"""


class _OversizedInt:
    """超过 Python 整数转换上限的整数字面量占位值。

    Python 3.11+ 的 json 对超过 4300 位的整数会在解析期抛裸 ValueError，
    导致整单中断。parse_int 钩子里捕获后以本占位返回，让其作为该字段
    的“非整数”错误与其余字段错误一次性聚齐。
    """

    __slots__ = ("digits",)

    def __init__(self, digits: int):
        self.digits = digits

    def __repr__(self) -> str:
        return f"<{self.digits} 位整数>"


def _parse_int(token: str):
    try:
        return int(token, 10)
    except ValueError:
        return _OversizedInt(len(token.lstrip("-+")))


def _reject_duplicate_keys(pairs: list[tuple]) -> dict:
    """json.loads 的 object_pairs_hook：映射内重复键直接报错。"""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _short_repr(value, limit: int = 32) -> str:
    """错误消息中的值表示：截断超长内容，且不触发大整数字符串化限制。"""
    if isinstance(value, _OversizedInt):
        return repr(value)
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def assemble_candidates(
    raw: str | bytes | None,
    notes: list[Note],
    groups: list[Group],
) -> dict[int, int]:
    """解析并校验候选位移表，错误按编号稳定聚齐后整单拒绝。

    返回 {音符编号: 整数位移}（按编号升序）；groups 为 build_groups
    得到的组模型，用于允许域与同和弦一致性校验。
    """
    if raw is None or not str(raw).strip():
        raise InputError(["候选 displacements 缺失或为空"], stage="verify")

    try:
        data = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_parse_int,
        )
    except _DuplicateKeyError as exc:
        raise InputError(
            [f"候选 displacements JSON 存在重复键 {exc.args[0]!r}"], stage="verify"
        ) from exc
    except ValueError as exc:
        # parse_int 已吸收超大整数；此处兜住其余解析期 ValueError
        raise InputError(
            [f"候选 displacements 不是合法 JSON: {exc}"], stage="verify"
        ) from exc
    except json.JSONDecodeError as exc:
        raise InputError(
            [f"候选 displacements 不是合法 JSON: {exc.msg}"], stage="verify"
        ) from exc

    if not isinstance(data, dict):
        raise InputError(
            [f"候选 displacements 必须是编号到位移的映射，得到 {type(data).__name__}"],
            stage="verify",
        )

    note_ids = {n.note_id for n in notes}
    note_by_id = {n.note_id: n for n in notes}
    valid: dict[int, int] = {}
    invalid_ids: set[int] = set()       # 已有更基本错误的编号（不再重复报同和弦）
    # (类别, 编号, 键)；按编号稳定汇总，真实编号在前，多余编号随后
    errors: list[tuple[tuple, str]] = []

    for key, value in data.items():
        try:
            nid = int(str(key), 10)
            canonical = str(nid) == str(key).strip()
        except (TypeError, ValueError):
            nid, canonical = None, False
        if not canonical or nid not in note_ids:
            # 无法规范成音符编号或清单中不存在：一律按多号处理（含 "1.0"、true）
            errors.append((
                (1, nid if canonical else 10**18, str(key)),
                f"候选出现音符清单中没有的编号 {_short_repr(key)}",
            ))
            continue
        if isinstance(value, _OversizedInt) or isinstance(value, bool) \
                or not isinstance(value, int):
            errors.append((
                (0, nid, ""),
                f"编号 {nid} 的位移必须是整数，得到 {_short_repr(value)}",
            ))
            invalid_ids.add(nid)
            continue
        note = note_by_id[nid]
        lo, hi = -note.before, note.after
        if not lo <= value <= hi:
            shown = value if abs(value) < 10**12 else _short_repr(value)
            errors.append((
                (0, nid, ""),
                f"编号 {nid} 的位移 {shown} 超出允许域 [{lo}, {hi}]",
            ))
            invalid_ids.add(nid)
            continue
        valid[nid] = value

    # 缺号（编号缺失与多号不重叠，统一按编号排序）
    for nid in sorted(note_ids - set(valid) - invalid_ids):
        errors.append(((0, nid, ""), f"候选缺少编号 {nid} 的位移"))

    # 同和弦成员位移必须一致：组内全部成员值齐全且为整数时才判定，
    # 缺号/非整数已单独成错，不重复报。
    for grp in groups:
        if grp.chord is None or len(grp.member_ids) < 2:
            continue
        member_nids = [notes[i].note_id for i in grp.member_ids]
        values = [valid.get(nid) for nid in member_nids]
        if any(v is None for v in values):
            continue
        if len(set(values)) != 1:
            head = min(member_nids)
            detail = ", ".join(
                f"编号 {nid} 位移 {valid[nid]}" for nid in member_nids
            )
            errors.append((
                (0, head, ""),
                f"同和弦组 {grp.chord!r} 成员位移不一致（{detail}）",
            ))

    if errors:
        raise InputError(
            [msg for _, msg in sorted(errors, key=lambda e: e[0])],
            stage="verify",
        )

    return dict(sorted(valid.items()))


# ---------------------------------------------------------------------------
# 复核判定（不搜索）
# ---------------------------------------------------------------------------

@dataclass
class VerifyConflict:
    id_a: int
    id_b: int
    track: int
    tick_a: int
    tick_b: int
    disp_a: int
    disp_b: int
    gap: int


@dataclass
class VerifyResult:
    verified: bool
    displacements: dict[int, int] = field(default_factory=dict)
    max_abs: int = 0
    total_abs: int = 0
    chord_displacements: dict[str, int] = field(default_factory=dict)
    conflict: VerifyConflict | None = None
    chain: list[int] | None = None


def verify(
    machine: Machine,
    notes: list[Note],
    candidates: dict[int, int],
) -> VerifyResult:
    """对给定候选位移表做安全复核：先同轨净距，后外扩矩形横贯。

    只判定候选值，不枚举任何替代位移。
    """
    ordered, groups = build_groups(notes)

    disp: dict[int, int] = {}
    for grp in groups:
        for i in grp.member_ids:
            disp[ordered[i].note_id] = candidates[ordered[i].note_id]

    adjusted = {n.note_id: n.tick + disp[n.note_id] for n in ordered}

    # 1) 同轨净距：同轨全部孔对逐一判定，保留编号对最小的实际冲突
    by_track: dict[int, list[Note]] = {}
    for n in ordered:
        by_track.setdefault(n.track, []).append(n)

    min_pair: tuple[int, int] | None = None
    min_gap = 0
    for track_notes in by_track.values():
        for ai, na in enumerate(track_notes):
            for nb in track_notes[ai + 1:]:
                ta, tb = adjusted[na.note_id], adjusted[nb.note_id]
                gap = machine.same_track_gap(ta, tb)
                if gap < machine.min_clearance:
                    pair = tuple(sorted((na.note_id, nb.note_id)))
                    if min_pair is None or pair < min_pair:
                        min_pair, min_gap = pair, gap

    if min_pair is not None:
        id_a, id_b = min_pair
        na = next(n for n in ordered if n.note_id == id_a)
        nb = next(n for n in ordered if n.note_id == id_b)
        return VerifyResult(
            verified=False,
            displacements=dict(sorted(disp.items())),
            conflict=VerifyConflict(
                id_a=id_a,
                id_b=id_b,
                track=na.track,
                tick_a=adjusted[id_a],
                tick_b=adjusted[id_b],
                disp_a=disp[id_a],
                disp_b=disp[id_b],
                gap=min_gap,
            ),
        )

    # 2) 外扩矩形横贯：在候选向量上求字典序最小的真实贯通链
    group_values = [0] * len(groups)
    for grp in groups:
        head_nid = ordered[grp.member_ids[0]].note_id
        group_values[grp.index] = disp[head_nid]

    chain = find_smallest_chain(machine, ordered, group_values)
    if chain is not None:
        return VerifyResult(
            verified=False,
            displacements=dict(sorted(disp.items())),
            chain=chain,
        )

    max_abs = max((abs(d) for d in disp.values()), default=0)
    total_abs = sum(abs(d) for d in disp.values())
    chord_disps = {
        grp.chord: disp[ordered[grp.member_ids[0]].note_id]
        for grp in groups
        if grp.chord is not None
    }
    return VerifyResult(
        verified=True,
        displacements=dict(sorted(disp.items())),
        max_abs=max_abs,
        total_abs=total_abs,
        chord_displacements=dict(sorted(chord_disps.items())),
    )
