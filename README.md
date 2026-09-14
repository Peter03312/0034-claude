# 纸卷打孔时序求解 API

纯后端服务：给定**机型尺寸**与**逐音符目标 tick（含前后微调量、和弦组）**，
完整搜索一个确定的整数位移方案，使打孔既满足**同轨最小净距**，又不会因
相邻轨道长孔经安全桥外扩后错落搭接而形成**横贯整张纸宽的薄弱链**。

逐音符贪心“取最近时刻”会把本可以错开的谱段排死（见
`examples/` 与 `tests/test_greedy_fail.py`），本服务不使用贪心，
而是带增量安全剪枝的完整搜索。

## 目标（按字典序最小化）

1. 最大绝对位移 `max |d|`
2. 总绝对位移 `Σ|d|`
3. 按音符编号升序排列的位移向量

三者完全确定，重复请求结果一致。

## 约束

- **同和弦组共享一个整数位移**，组允许域取各音符 `[-before, after]` 的交集。
- 孔中心 =（轨道中心横坐标, `(目标 tick + 位移) × 每 tick 长度`）。
- **同轨净距**：同一轨道两孔纵向闭区间净距 `|Δt|·tick_length − hole_height`
  不得低于 `min_clearance`（恰好相等合法）。
- **横贯薄弱链**：每个孔矩形四边各外扩 `bridge_width/2`；任意两孔外扩矩形
  闭区间接触或重叠即连通；一个连通分量同时抵达 `x ≤ 0` 与
  `x ≥ paper_width` 则该方案非法。
- 所有尺寸同一整数单位；安全桥外扩产生的半整数在内部统一放大 2 倍做
  整数运算，无浮点误差。

## 无解时的真实诊断（不返回占位值）

- 若**没有任何组合**通过同轨净距：穷举所有完整组合，返回其中**编号对最小**
  的实际冲突（双方编号、轨道、调整后 tick、位移、实测净距）。
- 若通过净距的组合**全部**形成横贯链：在其中取目标序最小的位移向量，
  返回该方案上**孔编号序列字典序最小的真实贯通链**（首孔触左边、
  末孔触右边、相邻孔外扩矩形真实接触）。

## 输入

### 机型 YAML

```yaml
tick_length: 10        # 每 tick 纵向长度
hole_width: 26         # 孔模横宽
hole_height: 11        # 孔模纵长
paper_width: 60        # 纸宽
min_clearance: 8       # 同轨最小净距
bridge_width: 2        # 安全桥宽（四边各外扩其一半）
tracks:
  - {id: 1, x: 14}     # 轨道编号、轨道中心横坐标
  - {id: 2, x: 30}
  - {id: 3, x: 46}
# tracks 也支持映射写法：{1: 14, 2: 30, 3: 46}
```

### 逐音符 CSV

表头固定为 `id,track,tick,before,after,chord`：

| 列 | 含义 |
|---|---|
| `id` | 唯一整数编号 |
| `track` | 机型中存在的轨道编号 |
| `tick` | 目标整数 tick |
| `before` | 允许向前微调量（非负整数，位移下界 `-before`） |
| `after` | 允许向后微调量（非负整数，位移上界 `after`） |
| `chord` | 和弦组标识；留空表示该音符独立成组 |

## 运行

```bash
# 构建并常驻 API（唯一常驻服务）
docker compose up -d --build

# 覆盖宿主机映射端口（容器内固定 8000）
API_PORT=9000 docker compose up -d

# 一次性验证：pytest 全量测试 + API 启动探测，结束即退出
docker compose run --rm verify
```

本地（Python 3.12）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
python scripts/startup_probe.py
python -m pytest -q
```

## API

### `GET /health`

```json
{"status": "ok"}
```

### `POST /solve`

`multipart/form-data`：

- `machine`：机型 YAML 文件
- `notes`：逐音符 CSV 文件

**可行**（`200`）：

```json
{
  "status": "feasible",
  "objective": {"max_abs_displacement": 2, "total_abs_displacement": 4},
  "displacements": {"1": 0, "2": 2, "3": 0, "4": 2},
  "chord_displacements": {"C1": 2},
  "holes": [
    {
      "id": 1, "track": 1, "displacement": 0, "adjusted_tick": 10,
      "center": {"x": 14, "y": 100},
      "hole_rect":     {"x0": 1, "y0": 94.5, "x1": 27, "y1": 105.5},
      "expanded_rect": {"x0": 0, "y0": 93.5, "x1": 28, "y1": 106.5}
    }
  ]
}
```

**必然净距冲突**（`200`，业务状态）：

```json
{
  "status": "infeasible_clearance",
  "conflict": {
    "id_a": 2, "id_b": 3, "track": 2,
    "adjusted_tick_a": 8, "adjusted_tick_b": 7,
    "displacement_a": 0, "displacement_b": 0,
    "gap": -2, "required_min_clearance": 8
  }
}
```

**必然横贯**（`200`）：`status = "infeasible_chain"`，
携带 `weak_chain.note_ids`（字典序最小真实链）、对应位移与逐孔矩形。

**坏输入整单拒绝**（`422`）：YAML/CSV 的所有错误一次性聚齐返回，
不执行任何求解：

```json
{"status": "invalid_input", "message": "输入装配失败，整单拒绝（未执行求解）",
 "errors": ["...", "..."]}
```

## 项目结构

```
app/
  models.py   # YAML/CSV 输入装配、机型与孔区几何（放大 2 倍整数运算）
  solver.py   # 组模型、回滚并查集、增量净距/连通剪枝、完整搜索与诊断
  main.py     # FastAPI 路由与响应装配
scripts/
  startup_probe.py   # verify 服务使用的一次性启动探测
tests/
  test_greedy_fail.py   # 贪心失败而全局有解（含和弦组）
  test_clearance.py     # 临界净距 / 必然冲突 / 最小编号对
  test_chain.py         # 必然贯通与真实链
  test_tie.py           # 稳定并列（字典序 + 重复确定性 + 行序无关）
  test_bad_input.py     # 坏输入整单拒绝、错误聚齐
  test_bruteforce.py    # 随机实例与独立朴素穷举逐一对照
```

## 求解器要点

- 组按组内最小音符编号排序，因此“按编号升序的位移向量”的字典序与
  组向量字典序一致。
- 候选位移按 `0, -1, +1, -2, +2, …` 枚举，优先取得低和现任解，
  配合总位移上界与等和前缀字典序剪枝。
- 连通性用**回滚并查集**（仅按大小合并、无路径压缩）维护：落一个孔只与
  已落孔做矩形接触判定，出现 `LEFT/RIGHT` 贯通立即剪枝并整体回滚。
- 无解诊断阶段**关闭剪枝**探到每个完整组合：否则早序孔冲突会遮蔽同一组合
  中编号对更小的冲突（该问题由穷举交叉测试发现并修复）。
