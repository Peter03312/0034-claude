"""纸卷打孔时序求解 —— 纯后端 API。

POST /solve  multipart 上传：
  machine: 机型 YAML 文件
  notes:   逐音符 CSV 文件
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .models import InputError, Machine, load_machine, load_notes
from .solver import SolveResult, solve

app = FastAPI(
    title="纸卷打孔时序求解 API",
    version="1.0.0",
    description="完整搜索音符位移：同轨净距 + 安全桥横贯薄弱链约束。",
)


def _num(value: int) -> int | float:
    """放大 2 倍的内部整数还原为外部数值：偶数为整数，奇数为半整数。"""
    q, r = divmod(value, 2)
    return q if r == 0 else q + 0.5


def _hole_payload(machine: Machine, track: int, adjusted_tick: int) -> dict:
    x0, y0, x1, y1 = machine.scaled_rect(track, adjusted_tick)
    ex0, ey0, ex1, ey1 = machine.expanded_rect(track, adjusted_tick)
    return {
        "track": track,
        "center": {
            "x": machine.tracks[track],
            "y": _num(adjusted_tick * machine.s2_tick_length),
        },
        "adjusted_tick": adjusted_tick,
        "hole_rect": {
            "x0": _num(x0), "y0": _num(y0),
            "x1": _num(x1), "y1": _num(y1),
        },
        "expanded_rect": {
            "x0": _num(ex0), "y0": _num(ey0),
            "x1": _num(ex1), "y1": _num(ey1),
        },
    }


def _result_payload(machine: Machine, notes, result: SolveResult) -> dict:
    if result.feasible:
        holes = []
        for note in sorted(notes, key=lambda n: n.note_id):
            d = result.displacements[note.note_id]
            item = {"id": note.note_id, "displacement": d}
            item.update(_hole_payload(machine, note.track, note.tick + d))
            holes.append(item)
        return {
            "status": "feasible",
            "objective": {
                "max_abs_displacement": result.max_abs,
                "total_abs_displacement": result.total_abs,
            },
            "displacements": result.displacements,
            "chord_displacements": result.chord_displacements,
            "holes": holes,
        }

    if result.min_conflict is not None:
        c = result.min_conflict
        return {
            "status": "infeasible_clearance",
            "reason": "no_assignment_passes_same_track_clearance",
            "message": (
                f"无任何组合通过同轨净距；穷举所得编号对最小的实际冲突："
                f"孔 {c.id_a} 与 {c.id_b}（轨道 {c.track}），"
                f"调整后 tick {c.tick_a}/{c.tick_b}，净距 {c.gap}"
            ),
            "conflict": {
                "id_a": c.id_a,
                "id_b": c.id_b,
                "track": c.track,
                "adjusted_tick_a": c.tick_a,
                "adjusted_tick_b": c.tick_b,
                "displacement_a": c.disp_a,
                "displacement_b": c.disp_b,
                "gap": c.gap,
                "required_min_clearance": machine.min_clearance,
            },
        }

    assert result.chain is not None and result.chain_displacements is not None
    holes = []
    note_by_id = {n.note_id: n for n in notes}
    for nid in result.chain:
        note = note_by_id[nid]
        d = result.chain_displacements[nid]
        item = {"id": nid, "displacement": d}
        item.update(_hole_payload(machine, note.track, note.tick + d))
        holes.append(item)
    return {
        "status": "infeasible_chain",
        "reason": "every_clearance_valid_assignment_forms_left_right_chain",
        "message": (
            "通过同轨净距的组合均形成横贯薄弱链；给出目标序最小位移向量上"
            "孔编号序列字典序最小的真实贯通链"
        ),
        "objective": {
            "max_abs_displacement": result.chain_max_abs,
            "total_abs_displacement": result.chain_total_abs,
        },
        "displacements": result.chain_displacements,
        "weak_chain": {
            "note_ids": result.chain,
            "sequence_description": "左纸边 -> 孔（外扩矩形接触搭接）-> 右纸边",
            "holes": holes,
        },
    }


@app.exception_handler(InputError)
async def input_error_handler(_request, exc: InputError):
    return JSONResponse(
        status_code=422,
        content={
            "status": "invalid_input",
            "message": "输入装配失败，整单拒绝（未执行求解）",
            "errors": exc.messages,
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(_request, exc: ValueError):
    """装配层漏网的非法输入：报 422 而不是 500（求解器自身不抛 ValueError）。"""
    return JSONResponse(
        status_code=422,
        content={
            "status": "invalid_input",
            "message": "输入不合法，整单拒绝（未执行求解）",
            "errors": [str(exc)],
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/solve")
async def solve_endpoint(
    machine: UploadFile = File(..., description="机型 YAML"),
    notes: UploadFile = File(..., description="逐音符 CSV"),
):
    try:
        machine_text = (await machine.read()).decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(["机型文件不是合法 UTF-8 文本"])
    try:
        notes_text = (await notes.read()).decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(["音符 CSV 不是合法 UTF-8 文本"])

    try:
        machine_model = load_machine(machine_text)
        note_models = load_notes(notes_text, machine_model)
    except InputError:
        raise

    result = solve(machine_model, note_models)
    return _result_payload(machine_model, note_models, result)
