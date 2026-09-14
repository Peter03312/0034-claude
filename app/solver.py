"""打孔时序求解器。

搜索空间
--------
音符允许域为整数位移区间 [-before, after]。同一 chord 组共享一个整数位移，
组允许域取各音符允许域交集；chord 为空的音符各自成组。

判定
----
1. 同轨净距：同一轨道任意两孔的纵向闭区间净距（未外扩孔矩形）不得低于
   min_clearance。
2. 横贯薄弱链：孔矩形四边各外扩安全桥宽的一半，外扩矩形闭区间接触或重叠即
   连通；一个连通分量同时抵达 x<=0 与 x>=纸宽 则非法。

搜索
----
完整回溯，按组依次枚举；两层增量安全剪枝：
  * 落孔时只与已落孔做同轨净距检查，冲突立即剪枝；
  * 用回滚并查集（只按大小合并、无路径压缩）增量维护外扩矩形连通性，落孔后
    一旦出现左右横贯分量立即剪枝。
目标按字典序最小化 (最大绝对位移, 总绝对位移, 按编号升序的位移向量)。
M 由小到大迭代；首个可行 M 的搜索中以完全字典序 DFS 取得最优向量。

无解诊断（穷举真实证据，禁止占位）
----------------------------------
* 若没有任何组合通过同轨净距：返回穷举中见到的编号对最小的实际冲突；
* 否则在全部通过净距的组合里取目标序最小的位移向量，返回其上孔编号序列
  字典序最小的真实贯通链。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Sequence

from .models import Machine, Note

LEFT = -1   # 虚拟节点：左纸边
RIGHT = -2  # 虚拟节点：右纸边

sys.setrecursionlimit(10000)


# ---------------------------------------------------------------------------
# 组模型（求解与复核共用的公共判定）
# ---------------------------------------------------------------------------

def build_groups(
    notes: list[Note],
) -> tuple[list[Note], list["Group"]]:
    """从音符清单构造组模型并回填每个音符的 group_index。

    同 chord 的音符共享一组（允许域取交集），chord 为空者各自成组；
    组按组内最小音符编号升序排列，音符按编号升序返回。
    """
    ordered = sorted(notes, key=lambda n: n.note_id)

    chord_map: dict[str, list[int]] = {}
    solo: list[int] = []
    for i, note in enumerate(ordered):
        if note.chord is None:
            solo.append(i)
        else:
            chord_map.setdefault(note.chord, []).append(i)

    groups: list[Group] = []
    for i in solo:
        n = ordered[i]
        groups.append(Group(len(groups), None, [i], -n.before, n.after))
    for chord, members in chord_map.items():
        members.sort(key=lambda i: ordered[i].note_id)
        lo = max(-ordered[i].before for i in members)
        hi = min(ordered[i].after for i in members)
        groups.append(Group(len(groups), chord, members, lo, hi))

    # 组序按组内最小编号升序：按编号升序的音符位移向量与组序向量
    # 的字典序完全一致（组内位移相同，首编号互不相同）。
    groups.sort(key=lambda grp: min(ordered[i].note_id for i in grp.member_ids))
    for new_idx, grp in enumerate(groups):
        grp.index = new_idx
        for i in grp.member_ids:
            ordered[i].group_index = new_idx
    return ordered, groups


# ---------------------------------------------------------------------------
# 回滚并查集
# ---------------------------------------------------------------------------

class RollbackDSU:
    """只按大小合并、无路径压缩的可回滚并查集。"""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.size: dict[int, int] = {}

    def add(self, x: int) -> None:
        self.parent[x] = x
        self.size[x] = 1

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]

    def same(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)

    def checkpoint(self) -> tuple[dict[int, int], dict[int, int]]:
        return dict(self.parent), dict(self.size)

    def rollback(self, state: tuple[dict[int, int], dict[int, int]]) -> None:
        self.parent, self.size = dict(state[0]), dict(state[1])


# ---------------------------------------------------------------------------
# 组与结果
# ---------------------------------------------------------------------------

@dataclass
class Group:
    index: int
    chord: str | None               # None 表示单音符独立组
    member_ids: list[int]           # 音符在 notes 列表中的下标
    lo: int                         # 允许位移下界
    hi: int                         # 允许位移上界

    @property
    def weight(self) -> int:
        return len(self.member_ids)


@dataclass
class Conflict:
    id_a: int
    id_b: int
    track: int
    tick_a: int
    tick_b: int
    disp_a: int
    disp_b: int
    gap: int                        # 实际净距（原单位，可为负）


@dataclass
class SolveResult:
    feasible: bool
    displacements: dict[int, int] = field(default_factory=dict)
    max_abs: int = 0
    total_abs: int = 0
    chord_displacements: dict[str, int] = field(default_factory=dict)
    min_conflict: Conflict | None = None
    chain: list[int] | None = None
    chain_displacements: dict[int, int] | None = None
    chain_max_abs: int = 0
    chain_total_abs: int = 0


class Solver:
    def __init__(self, machine: Machine, notes: list[Note]):
        self.m = machine
        self.notes, self.groups = build_groups(notes)
        self.n = len(self.notes)
        self.track_of = [n.track for n in self.notes]
        self.by_track: dict[int, list[int]] = {}
        for i, n in enumerate(self.notes):
            self.by_track.setdefault(n.track, []).append(i)
        self.g = len(self.groups)

        # 搜索工作区
        self.val: list[int] = [0] * self.g          # 每组当前位移
        self.disp: list[int] = [0] * self.n         # 每音符当前位移
        self.active: list[int] = []                 # 已落孔音符下标
        self.dsu = RollbackDSU()
        self.dsu.add(LEFT)
        self.dsu.add(RIGHT)

        # 外扩矩形 x 范围与 tick 无关，预计算每孔触边情况
        self.touches_l: list[bool] = []
        self.touches_r: list[bool] = []
        pw2 = self.m.s2_paper_width
        for n in self.notes:
            r = self.m.expanded_rect(n.track, 0)
            self.touches_l.append(r[0] <= 0)
            self.touches_r.append(r[2] >= pw2)

        # 最优解
        self.best: list[int] | None = None
        self.best_sum = 0

        # 诊断
        self.min_conflict: tuple | None = None      # (pair, vals_tuple, ta, tb, gap)
        self.diag_best: list[int] | None = None
        self.diag_best_max = 0
        self.diag_best_sum = 0

    # ------------------------------------------------------------- 候选
    def _candidates(self, grp: Group, cap: int) -> list[int]:
        """组在 |d|<=cap 下的候选位移，按目标偏好序：0, -1, +1, -2, +2..."""
        lo = max(grp.lo, -cap)
        hi = min(grp.hi, cap)
        out: list[int] = []
        if lo <= 0 <= hi:
            out.append(0)
        k = 1
        while k <= max(-lo, hi, 0):
            if -k >= lo:
                out.append(-k)
            if k <= hi:
                out.append(k)
            k += 1
        return out

    # ------------------------------------------------------- 落孔 / 回滚
    def _place(
        self, gi: int, d: int
    ) -> tuple[bool, tuple[int, int] | None, int]:
        """落下一组的全部孔，增量执行两类安全检查。

        成功返回 (True, None, 0)；失败时内部已回滚到调用前状态，返回
        (False, 冲突编号对, 实际净距)（横贯剪枝时后两者为 None/0）。
        """
        grp = self.groups[gi]
        state = self.dsu.checkpoint()
        active_snapshot = len(self.active)
        # 本组已落成员（active 切片的起点固定，直接用切片视图即可）
        batch_start = active_snapshot

        def retreat() -> None:
            for q in self.active[batch_start:]:
                self.disp[q] = 0
            del self.active[batch_start:]
            self.dsu.rollback(state)

        for i in grp.member_ids:
            self.disp[i] = d
            note = self.notes[i]
            tick_i = note.tick + d

            # 1) 同轨净距（未外扩矩形）：先前组已落孔 + 本组已落孔
            conflict_idx = -1
            conflict_gap = 0
            for j in self.active:
                if self.track_of[j] != note.track:
                    continue
                gap = self.m.same_track_gap(
                    tick_i, self.notes[j].tick + self.disp[j]
                )
                if gap < self.m.min_clearance:
                    conflict_idx, conflict_gap = j, gap
                    break
            if conflict_idx >= 0:
                pair = tuple(sorted((note.note_id,
                                     self.notes[conflict_idx].note_id)))
                retreat()
                return False, pair, conflict_gap

            # 2) 注册连通节点，与所有已落孔增量合并外扩矩形接触
            self.dsu.add(i)
            if self.touches_l[i]:
                self.dsu.union(i, LEFT)
            if self.touches_r[i]:
                self.dsu.union(i, RIGHT)
            for j in self.active:
                if self._expanded_contact(i, j, tick_i):
                    self.dsu.union(i, j)

            self.active.append(i)

            # 横贯剪枝：出现同时触左右边的分量即非法
            if self.dsu.same(LEFT, RIGHT):
                retreat()
                return False, None, 0

        return True, None, 0

    def _expanded_contact(self, i: int, j: int, tick_i: int) -> bool:
        """两孔外扩矩形闭区间是否接触或重叠（j 已落孔）。"""
        ri = self.m.expanded_rect(self.notes[i].track, tick_i)
        rj = self.m.expanded_rect(
            self.notes[j].track, self.notes[j].tick + self.disp[j]
        )
        return (
            ri[0] <= rj[2] and rj[0] <= ri[2]
            and ri[1] <= rj[3] and rj[1] <= ri[3]
        )

    # ------------------------------------------------------------- 搜索
    def solve(self) -> SolveResult:
        m_max = max(max(-g.lo, g.hi) for g in self.groups)

        # 阶段一：M 由小到大，首个可行 M 即最小最大绝对位移
        for cap in range(0, m_max + 1):
            self.best = None
            self.best_sum = 0
            self._dfs_legal(0, cap, 0)
            if self.best is not None:
                return self._build_feasible()

        # 阶段二：无合法组合。全允许域上仅做净距的完全枚举，
        # 收集编号对最小的实际冲突，以及目标序最小的通过净距组合。
        self.val = [0] * self.g
        self._dfs_clearance_only(0, 0)

        if self.diag_best is None:
            return SolveResult(feasible=False, min_conflict=self._build_conflict())

        chain = self._smallest_chain(self.diag_best)
        assert chain is not None, "通过净距的组合被判定横贯，却找不到贯通链"
        disps = {
            self.notes[i].note_id: self.diag_best[self.notes[i].group_index]
            for i in range(self.n)
        }
        return SolveResult(
            feasible=False,
            chain=chain,
            chain_displacements=dict(sorted(disps.items())),
            chain_max_abs=self.diag_best_max,
            chain_total_abs=self.diag_best_sum,
        )

    def _dfs_legal(self, k: int, cap: int, cur_sum: int) -> None:
        if self.best is not None:
            if cur_sum > self.best_sum:
                return
            # 等和时已提交前缀在首个差异位上更大，后续只能加非负位移，
            # 既追不平字典序也不可能更小和（和已相等），安全剪枝。
            if cur_sum == self.best_sum:
                for t in range(k):
                    if self.val[t] != self.best[t]:
                        if self.val[t] > self.best[t]:
                            return
                        break

        if k == self.g:
            cand = tuple(self.val)
            if (
                self.best is None
                or (cur_sum, cand) < (self.best_sum, tuple(self.best))
            ):
                self.best = list(cand)
                self.best_sum = cur_sum
            return

        grp = self.groups[k]
        for d in self._candidates(grp, cap):
            new_sum = cur_sum + grp.weight * abs(d)
            if self.best is not None and new_sum > self.best_sum:
                continue
            self.val[k] = d
            state = self.dsu.checkpoint()
            active_len = len(self.active)
            ok, _pair, _gap = self._place(k, d)
            if ok:
                self._dfs_legal(k + 1, cap, new_sum)
            for q in self.active[active_len:]:
                self.disp[q] = 0
            del self.active[active_len:]
            self.dsu.rollback(state)
            self.val[k] = 0

    def _dfs_clearance_only(self, k: int, cur_sum: int) -> None:
        """全允许域、不做剪枝的完全枚举：必须探到每个完整组合。

        净距剪枝会在早序孔冲突后放弃后续孔，从而漏看同一完整组合里
        编号对更小的实际冲突；因此诊断阶段在叶端统一检查全部同轨孔对。
        """
        if k == self.g:
            min_pair: tuple[int, int] | None = None
            min_gap = 0
            for idxs in self.by_track.values():
                for ai, i in enumerate(idxs):
                    gi = self.notes[i].group_index
                    for j in idxs[ai + 1:]:
                        gj = self.notes[j].group_index
                        gap = self.m.same_track_gap(
                            self.notes[i].tick + self.val[gi],
                            self.notes[j].tick + self.val[gj],
                        )
                        if gap < self.m.min_clearance:
                            pair = tuple(
                                sorted((self.notes[i].note_id, self.notes[j].note_id))
                            )
                            if min_pair is None or pair < min_pair:
                                min_pair, min_gap = pair, gap

            if min_pair is not None:
                self._record_conflict(min_pair, min_gap)
                return

            cur_max = max((abs(self.val[t]) for t in range(self.g)), default=0)
            cand = tuple(self.val)
            if (
                self.diag_best is None
                or (cur_max, cur_sum, cand)
                < (self.diag_best_max, self.diag_best_sum, tuple(self.diag_best))
            ):
                self.diag_best = list(cand)
                self.diag_best_max = cur_max
                self.diag_best_sum = cur_sum
            return

        grp = self.groups[k]
        for d in self._candidates(grp, max(-grp.lo, grp.hi)):
            self.val[k] = d
            self._dfs_clearance_only(k + 1, cur_sum + grp.weight * abs(d))
            self.val[k] = 0

    def _record_conflict(self, pair: tuple[int, int], gap: int) -> None:
        """穷举中见到实际冲突，保留编号对最小者（完整位移向量一并存证）。"""
        if self.min_conflict is not None and pair >= self.min_conflict[0]:
            return
        id_a, id_b = pair
        ia = next(i for i, nt in enumerate(self.notes) if nt.note_id == id_a)
        ib = next(i for i, nt in enumerate(self.notes) if nt.note_id == id_b)
        da = self.val[self.notes[ia].group_index]
        db = self.val[self.notes[ib].group_index]
        self.min_conflict = (
            pair,
            tuple(self.val),
            self.notes[ia].tick + da,
            self.notes[ib].tick + db,
            gap,
        )

    # ------------------------------------------------- 最小贯通链
    def _smallest_chain(self, assignment: list[int]) -> list[int] | None:
        return find_smallest_chain(self.m, self.notes, assignment)

    def _seq(self, path: list[int]) -> list[int]:
        return [self.notes[i].note_id for i in path]

    # ------------------------------------------------------------- 输出
    def _build_feasible(self) -> SolveResult:
        assert self.best is not None
        disps: dict[int, int] = {}
        chord_disps: dict[str, int] = {}
        total = 0
        for grp in self.groups:
            d = self.best[grp.index]
            if grp.chord is not None:
                chord_disps[grp.chord] = d
            for i in grp.member_ids:
                disps[self.notes[i].note_id] = d
                total += abs(d)
        mx = max((abs(d) for d in disps.values()), default=0)
        return SolveResult(
            feasible=True,
            displacements=dict(sorted(disps.items())),
            max_abs=mx,
            total_abs=total,
            chord_displacements=dict(sorted(chord_disps.items())),
        )

    def _build_conflict(self) -> Conflict:
        assert self.min_conflict is not None
        pair, vals, ta, tb, gap = self.min_conflict
        id_a, id_b = pair
        ia = next(i for i, nt in enumerate(self.notes) if nt.note_id == id_a)
        ib = next(i for i, nt in enumerate(self.notes) if nt.note_id == id_b)
        return Conflict(
            id_a=id_a,
            id_b=id_b,
            track=self.notes[ia].track,
            tick_a=ta,
            tick_b=tb,
            disp_a=vals[self.notes[ia].group_index],
            disp_b=vals[self.notes[ib].group_index],
            gap=gap,
        )


def find_smallest_chain(
    machine: Machine,
    notes: Sequence[Note],
    group_values: Sequence[int],
) -> list[int] | None:
    """在给定位移向量上，求孔编号序列字典序最小的真实左右贯通链。

    notes 需按编号升序排列（即 build_groups 的返回顺序），group_values[g]
    为第 g 组的位移。图节点为孔，外扩矩形接触/重叠连边；完整 DFS，按邻居
    编号升序枚举，用已知最优同长度前缀做安全剪枝。复核层直接复用此判定。
    """
    rects: dict[int, tuple[int, int, int, int]] = {}
    for i, n in enumerate(notes):
        rects[i] = machine.expanded_rect(
            n.track, n.tick + group_values[n.group_index]
        )

    ids = list(rects)
    adj: dict[int, list[int]] = {i: [] for i in ids}
    for ai in range(len(ids)):
        i = ids[ai]
        ri = rects[i]
        for j in ids[ai + 1:]:
            rj = rects[j]
            if (
                ri[0] <= rj[2] and rj[0] <= ri[2]
                and ri[1] <= rj[3] and rj[1] <= ri[3]
            ):
                adj[i].append(j)
                adj[j].append(i)
    # notes 已按编号升序，建边追加即为升序；稳妥起见再排序。
    for i in adj:
        adj[i].sort(key=lambda x: notes[x].note_id)

    left_nodes = sorted(
        (i for i, r in rects.items() if machine.touches_left_edge(r)),
        key=lambda i: notes[i].note_id,
    )
    right_set = {i for i, r in rects.items() if machine.touches_right_edge(r)}

    def seq(path: list[int]) -> list[int]:
        return [notes[i].note_id for i in path]

    best: list[int] | None = None

    def dfs(path: list[int], visited: set[int]) -> None:
        nonlocal best
        last = path[-1]
        if last in right_set:
            if best is None or seq(path) < seq(best):
                best = list(path)
            return
        for nb in adj[last]:
            if nb in visited:
                continue
            if best is not None:
                trial = seq(path) + [notes[nb].note_id]
                bseq = seq(best)
                length = min(len(trial), len(bseq))
                if trial[:length] > bseq[:length]:
                    continue
            visited.add(nb)
            path.append(nb)
            dfs(path, visited)
            path.pop()
            visited.remove(nb)

    for start in left_nodes:
        if best is not None and notes[start].note_id > seq(best)[0]:
            break
        dfs([start], {start})

    if best is None:
        return None
    return seq(best)


def solve(machine: Machine, notes: list[Note]) -> SolveResult:
    return Solver(machine, notes).solve()
