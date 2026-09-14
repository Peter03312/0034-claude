"""穷举交叉验证：用独立的朴素实现枚举全部位移组合，与求解器逐一对照。

朴素实现直接在笛卡尔积上做完整判定（不做任何剪枝），保证求解器
既不返回固定答案，也不会把非法组合判成合法、漏掉全局最优或真实诊断。
"""

from __future__ import annotations

import itertools
import random

import pytest

from app.models import Machine, Note
from app.solver import Solver, solve


def brute_force(machine: Machine, notes: list[Note]):
    """返回 (best_vector_by_id, min_conflict_pair, chain_diagnostic)。

    best_vector_by_id: 目标序最优的“合法”位移（编号->位移），无则 None。
    min_conflict_pair: 所有组合中同轨净距实际冲突的最小编号对及见证，
                       若存在通过净距的组合则为 None。
    chain_on_best:     最优通过净距（但横贯）向量上的最小贯通链。
    """
    notes = sorted(notes, key=lambda n: n.note_id)

    groups: dict[str, list[Note]] = {}
    for n in notes:
        groups.setdefault(n.chord if n.chord is not None else f"__{n.note_id}", []).append(n)
    grp_list = list(groups.values())
    domains = [
        list(range(max(-n.before for n in g), min(n.after for n in g) + 1))
        for g in grp_list
    ]

    def rects_of(vector):
        rects, exps = {}, {}
        for g, d in zip(grp_list, vector):
            for n in g:
                rects[n.note_id] = machine.scaled_rect(n.track, n.tick + d)
                exps[n.note_id] = machine.expanded_rect(n.track, n.tick + d)
        return rects, exps

    def clearance_ok(rects):
        by_track: dict[int, list[int]] = {}
        for n in notes:
            by_track.setdefault(n.track, []).append(n.note_id)
        for ids in by_track.values():
            for a_i, a in enumerate(ids):
                for b in ids[a_i + 1:]:
                    ra, rb = rects[a], rects[b]
                    # 放大坐标下纵向净距：max(下界)-min(上界)，需 >= 2*min
                    gap2 = max(ra[1], rb[1]) - min(ra[3], rb[3])
                    if gap2 < machine.s2_min_clearance:
                        return False, tuple(sorted((a, b))), gap2 // 2
        return True, None, None

    def components(exps):
        ids = list(exps)
        adj = {i: set() for i in ids}
        for a_i, a in enumerate(ids):
            for b in ids[a_i + 1:]:
                ra, rb = exps[a], exps[b]
                if ra[0] <= rb[2] and rb[0] <= ra[2] and ra[1] <= rb[3] and rb[1] <= ra[3]:
                    adj[a].add(b)
                    adj[b].add(a)
        seen, comps = set(), []
        for i in ids:
            if i in seen:
                continue
            stack, comp = [i], set()
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(adj[x] - comp)
            comps.append(comp)
            seen |= comp
        return comps

    def has_chain(exps):
        pw2 = machine.s2_paper_width
        for comp in components(exps):
            left = any(exps[i][0] <= 0 for i in comp)
            right = any(exps[i][2] >= pw2 for i in comp)
            if left and right:
                return True
        return False

    def smallest_chain(exps):
        pw2 = machine.s2_paper_width
        ids = list(exps)
        adj = {i: set() for i in ids}
        for a_i, a in enumerate(ids):
            for b in ids[a_i + 1:]:
                ra, rb = exps[a], exps[b]
                if ra[0] <= rb[2] and rb[0] <= ra[2] and ra[1] <= rb[3] and rb[1] <= ra[3]:
                    adj[a].add(b)
                    adj[b].add(a)
        lefts = [i for i in ids if exps[i][0] <= 0]
        rights = {i for i in ids if exps[i][2] >= pw2}
        best = None

        def dfs(path, visited):
            nonlocal best
            if path[-1] in rights:
                if best is None or list(path) < best:
                    best = list(path)
                return
            for nb in sorted(adj[path[-1]]):
                if nb in visited:
                    continue
                trial = path + [nb]
                if best is not None:
                    L = min(len(trial), len(best))
                    if trial[:L] > best[:L]:
                        continue
                dfs(trial, visited | {nb})

        for start in sorted(lefts):
            dfs([start], {start})
        return best

    best_legal = None
    best_key = None
    best_clear = None
    best_clear_key = None
    min_pair = None

    for vector in itertools.product(*domains):
        disp = {}
        for g, d in zip(grp_list, vector):
            for n in g:
                disp[n.note_id] = d
        rects, exps = rects_of(vector)

        ok, pair, _ = clearance_ok(rects)
        vec_by_id = tuple(disp[k] for k in sorted(disp))
        if not ok:
            if min_pair is None or pair < min_pair:
                min_pair = pair
            continue

        mabs = max(abs(v) for v in vec_by_id)
        sabs = sum(abs(v) for v in vec_by_id)
        key = (mabs, sabs, vec_by_id)
        if best_clear is None or key < best_clear_key:
            best_clear, best_clear_key = dict(disp), key

        if not has_chain(exps):
            if best_legal is None or key < best_key:
                best_legal, best_key = dict(disp), key

    chain = None
    if best_legal is None and best_clear is not None:
        vector = []
        for g in grp_list:
            vector.append(best_clear[g[0].note_id])
        _, exps = rects_of(vector)
        chain = smallest_chain(exps)

    return best_legal, min_pair, chain, best_clear, best_clear_key


def _make_machine(tracks=(10, 30, 50), width=60, hw=26, hh=10, bridge=2, clearance=8):
    return Machine(
        tick_length=10,
        tracks={i + 1: x for i, x in enumerate(tracks)},
        hole_width=hw,
        hole_height=hh,
        paper_width=width,
        min_clearance=clearance,
        bridge_width=bridge,
    )


@pytest.mark.parametrize("seed", range(40))
def test_solver_matches_independent_bruteforce(seed):
    rng = random.Random(seed)
    n_tracks = rng.randint(1, 3)
    tracks = tuple(round((60 / (n_tracks + 1)) * (i + 1)) for i in range(n_tracks))
    machine = _make_machine(
        tracks=tracks,
        hw=rng.choice([20, 26, 32]),
        hh=rng.choice([8, 10, 12]),
        bridge=rng.choice([0, 2]),
        clearance=rng.choice([0, 8]),
    )
    n_notes = rng.randint(1, 5)
    used = set()
    notes = []
    for nid in range(1, n_notes + 1):
        track = rng.randint(1, n_tracks)
        tick = rng.randint(5, 15)
        before = rng.randint(0, 2)
        after = rng.randint(0, 2)
        chord = None
        if nid > 1 and rng.random() < 0.3:
            chord = f"C{rng.randint(1, 2)}"
        notes.append(Note(nid, track, tick, before, after, chord))
        used.add(nid)

    result = solve(machine, notes)
    best_legal, min_pair, chain, _best_clear, _ = brute_force(machine, notes)

    if best_legal is not None:
        assert result.feasible, f"seed {seed}: 朴素枚举存在合法解但求解器报无解"
        assert result.displacements == best_legal, (
            f"seed {seed}: 求解器 {result.displacements} != 最优 {best_legal}"
        )
    elif min_pair is not None:
        assert result.feasible is False and result.min_conflict is not None
        assert (result.min_conflict.id_a, result.min_conflict.id_b) == min_pair
    else:
        assert result.feasible is False and result.chain is not None
        assert result.chain == chain, f"seed {seed}: {result.chain} != {chain}"
