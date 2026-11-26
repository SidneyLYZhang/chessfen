# 国际象棋着法评价与关键转折整合计算方案



基于 Stockfish 的 `UCI_ShowWDL` 输出，同时计算“着法质量”和“关键转折”，并输出结构化结果。  

---

## 1. 设计原则

1. **视角统一**：所有计算统一到“走棋方 \(S\)”视角。  
   - 当前局面 \(F\) 由 \(S\) 走棋。
   - 实际着法为 \(P\)，走完后局面为 \(F_P\)，轮到对手 \(O\)。
2. **着法质量与转折判断分离**：  
   - **着法质量**回答：这步相对引擎最佳着法损失了多少胜率？
   - **关键转折**回答：走棋前后，胜/和/负概率分布是否发生结构性变化？
3. **优先使用 WDL**：`UCI_ShowWDL=true` 时，Stockfish 的 `wdl` 字段从**走棋方视角**给出胜/和/负概率，单位为千分比。  
4. **特殊标签是启发式叠加**：`Best`、`Brilliant`、`Great`、`Miss` 不替代基础分类，只作为附加标签。

---

## 2. 引擎配置

```text
setoption name UCI_ShowWDL value true
setoption name MultiPV value 1
```

推荐固定搜索条件，例如：

```text
go depth 18
```

或固定节点数：

```text
go nodes 1000000
```

**要求**：对 \(F\) 和 \(F_P\) 使用相同的 `depth` 或 `nodes`，并缓存分析结果。

---

## 3. 每步计算流程

### 3.1 分析当前局面 \(F\)

对 \(F\) 分析，得到：

- `bestmove = B`
- `score`：从 \(S\) 视角的 `cp` 或 `mate`
- `wdl_F = (W_F, D_F, L_F)`：从 \(S\) 视角的胜/和/负概率，归一化到 \(0\sim1\)

其中：

\[
W_F + D_F + L_F = 1
\]

### 3.2 走实际着法 \(P\)，分析 \(F_P\)

走 \(P\) 后得到 \(F_P\)，轮到对手 \(O\)。对 \(F_P\) 分析，得到：

- `wdl_P = (W_O, D_O, L_O)`：从 \(O\) 视角
- `cp_O` 或 `mate_O`：从 \(O\) 视角

转换为 \(S\) 视角：

\[
(W_{\text{after}}, D_{\text{after}}, L_{\text{after}}) = (L_O, D_O, W_O)
\]

\[
\text{cp}_{\text{after},S} = -\text{cp}_O
\]

若为将杀分数：

\[
\text{mate}_{\text{after},S} = -\text{mate}_O
\]

---

## 4. 着法质量判断

### 4.1 胜率损失

\[
\text{loss\_pct} = \max\left(0,\ (W_F - L_O) \times 100\right)
\]

解释：

- \(W_F\)：\(S\) 走最佳着法后的胜率。
- \(L_O\)：走完实际着法后，对手 \(O\) 的负率，即 \(S\) 的胜率。
- `loss_pct` 单位为**百分点**。

### 4.2 Lichess 风格分类阈值

| `loss_pct`         | 分类         |
| ------------------:| ------------ |
| \(0.0 \sim 4.9\)   | `Normal`     |
| \(5.0 \sim 9.9\)   | `Inaccuracy` |
| \(10.0 \sim 14.9\) | `Mistake`    |
| \(\ge 15.0\)       | `Blunder`    |

若 `P == B`，额外标记 `Best`。

### 4.3 可选 Chess.com 对照阈值

如果希望提供更细的标签，可额外按预期得分损失分类：

| 预期得分损失       | 可选标签     |
| ------------------:| ------------ |
| \(0\)              | `Best`       |
| \(0.00 \sim 0.02\) | `Excellent`  |
| \(0.02 \sim 0.05\) | `Good`       |
| \(0.05 \sim 0.10\) | `Inaccuracy` |
| \(0.10 \sim 0.20\) | `Mistake`    |
| \(\ge 0.20\)       | `Blunder`    |

**推荐**：主分类仍使用 Lichess 阈值；Chess.com 表只作为可选展示层，避免混用两套阈值。

---

## 5. 关键转折判断

### 5.1 WDL 向量距离

走棋前，\(S\) 视角：

\[
\mathbf{w}_{\text{before}} = (W_F, D_F, L_F)
\]

走棋后，\(S\) 视角：

\[
\mathbf{w}_{\text{after}} = (L_O, D_O, W_O)
\]

定义转折分数：

\[
S_{\text{turn}} =
|L_O - W_F| +
|D_O - D_F| +
|W_O - L_F|
\]

取值范围 \(0\sim2\)。

### 5.2 评估值变化

走棋方视角评估变化：

\[
\Delta \text{cp} =
\left| \text{cp}_{\text{after},S} - \text{cp}_F \right|
\]

其中：

- \(\text{cp}_F\)：\(F\) 的 `cp`，从 \(S\) 视角。
- \(\text{cp}_{\text{after},S} = -\text{cp}_O\)。

若出现 `mate`，优先进入将杀特殊判断。

### 5.3 转折分级

| 条件                                                         | 级别         |
| ------------------------------------------------------------ | ------------ |
| 将杀状态发生改变，或走后出现 `mate` 而走前不是 `mate`        | `决定性转折` |
| \(S_{\text{turn}} \ge 0.40\) 且 \(\Delta \text{cp} \ge 100\) | `决定性转折` |
| \(S_{\text{turn}} \ge 0.25\) 且 \(\Delta \text{cp} \ge 50\)  | `显著转折`   |
| \(S_{\text{turn}} \ge 0.15\) 且 \(\Delta \text{cp} \ge 30\)  | `轻度转折`   |
| 其他                                                         | `非转折`     |

**说明**：\(\Delta \text{cp}\) 门槛用于过滤均势附近 WDL 的轻微抖动。

---

## 6. 特殊标签叠加规则

以下标签为可选启发式，建议默认开启但允许关闭。

### 6.1 `Best`

```text
if P == B:
    tags.append("Best")
```

### 6.2 `Brilliant`

简化条件：

```text
if (P == B or loss_pct <= 1.0)
   and is_sacrifice(F, P)
   and win_after_S >= 0.50:
       tags.append("Brilliant")
```

其中：

```text
win_after_S = L_O
```

`is_sacrifice` 简化定义：

```text
material_after_S - material_before_S <= -100
```

即从走棋方视角，主动净损失至少一个兵的子力价值。兵值建议：兵=100，马/象=300，车=500，后=900。

### 6.3 `Great`

若启用 `MultiPV=3`，可定义：

```text
if P == B and second_best_loss_pct >= 10.0:
    tags.append("Great")
```

含义：最佳着法明显优于第二候选，属于“唯一好着”。

### 6.4 `Miss`

```text
if previous_opponent_move_class in ("Mistake", "Blunder")
   and loss_pct >= 10.0:
       tags.append("Miss")
```

含义：对手上一步给了机会，但本步没有抓住。

---

## 7. 推荐伪代码

```python
def analyze_position(fen, depth=18, multipv=1):
    # 返回 bestmove, cp, mate, wdl
    # wdl 从 side-to-move 视角，归一化到 0~1
    return bestmove, cp, mate, (w, d, l)

def analyze_game_step(F, P, depth=18):
    S_is_white = is_white_to_move(F)

    # 1. 分析当前局面 F
    B, cp_F, mate_F, (W_F, D_F, L_F) = analyze_position(F, depth, multipv=1)

    # 2. 走实际着法 P
    F_P = make_move(F, P)
    _, cp_O, mate_O, (W_O, D_O, L_O) = analyze_position(F_P, depth, multipv=1)

    # 3. 转换到走棋方 S 视角
    w_after_S = L_O
    d_after_S = D_O
    l_after_S = W_O

    cp_after_S = -cp_O if cp_O is not None else None
    mate_after_S = -mate_O if mate_O is not None else None

    # 4. 着法质量
    loss_pct = max(0.0, (W_F - L_O) * 100.0)

    if loss_pct < 5.0:
        move_class = "Normal"
    elif loss_pct < 10.0:
        move_class = "Inaccuracy"
    elif loss_pct < 15.0:
        move_class = "Mistake"
    else:
        move_class = "Blunder"

    tags = []
    if P == B:
        tags.append("Best")

    # 5. 转折分数
    S_turn = (
        abs(L_O - W_F) +
        abs(D_O - D_F) +
        abs(W_O - L_F)
    )

    # 6. 评估变化
    if cp_F is not None and cp_after_S is not None:
        delta_cp = abs(cp_after_S - cp_F)
    else:
        delta_cp = None

    # 7. 转折分级
    if mate_F is None and mate_after_S is not None:
        turning_level = "决定性转折"
    elif mate_F is not None and mate_after_S is None:
        turning_level = "决定性转折"
    elif mate_F is not None and mate_after_S is not None and mate_F * mate_after_S < 0:
        turning_level = "决定性转折"
    elif delta_cp is not None and S_turn >= 0.40 and delta_cp >= 100:
        turning_level = "决定性转折"
    elif delta_cp is not None and S_turn >= 0.25 and delta_cp >= 50:
        turning_level = "显著转折"
    elif delta_cp is not None and S_turn >= 0.15 and delta_cp >= 30:
        turning_level = "轻度转折"
    else:
        turning_level = "非转折"

    # 8. 可选特殊标签：Brilliant / Great / Miss
    # 按第 6 节规则叠加

    return {
        "bestmove": B,
        "move": P,
        "loss_pct": round(loss_pct, 1),
        "move_class": move_class,
        "tags": tags,
        "turning_score": round(S_turn, 3),
        "turning_level": turning_level,
        "wdl_before_S": [W_F, D_F, L_F],
        "wdl_after_S": [w_after_S, d_after_S, l_after_S],
        "eval_before_cp_S": cp_F,
        "eval_after_cp_S": cp_after_S,
        "delta_cp": delta_cp,
    }
```

---

## 8. 输出结构建议

```json
{
  "ply": 24,
  "move": "Nxe5",
  "bestmove": "Nxe5",
  "eval_before_cp_S": 35,
  "eval_after_cp_S": -120,
  "wdl_before_S": [0.45, 0.50, 0.05],
  "wdl_after_S": [0.30, 0.35, 0.35],
  "loss_pct": 15.0,
  "move_class": "Blunder",
  "tags": ["Miss"],
  "turning_score": 0.30,
  "turning_level": "显著转折"
}
```

---

## 9. 参数集中表

| 参数               | 推荐值 | 说明                     |
| ------------------ | ------:| ------------------------ |
| `ENGINE_DEPTH`     | 18     | 可改为固定节点数         |
| `MULTIPV_MAIN`     | 1      | 主分析                   |
| `MULTIPV_GREAT`    | 3      | 可选，用于 `Great`       |
| `WDL_UNIT`         | 1000   | Stockfish `wdl` 为千分比 |
| `INACCURACY_PCT`   | 5.0    | Lichess 阈值             |
| `MISTAKE_PCT`      | 10.0   | Lichess 阈值             |
| `BLUNDER_PCT`      | 15.0   | Lichess 阈值             |
| `TURN_LIGHT`       | 0.15   | 轻度转折 WDL 门槛        |
| `TURN_SIGNIFICANT` | 0.25   | 显著转折 WDL 门槛        |
| `TURN_DECISIVE`    | 0.40   | 决定性转折 WDL 门槛      |
| `CP_LIGHT`         | 30     | 轻度转折 cp 门槛         |
| `CP_SIGNIFICANT`   | 50     | 显著转折 cp 门槛         |
| `CP_DECISIVE`      | 100    | 决定性转折 cp 门槛       |
| `SACRIFICE_CP`     | 100    | 弃子判定净损失门槛       |
| `WIN_AFTER_MIN`    | 0.50   | `Brilliant` 要求走后不差 |

---

## 10. 降级方案

如果无法开启 `UCI_ShowWDL`：

1. 用 Lichess 公式从 `cp` 计算胜率：

\[
\text{Win\%} = 50 + 50 \times \left( \frac{2}{1 + e^{-0.00368208 \times \text{cp}}} - 1 \right)
\]

2. 着法质量仍可用：

\[
\text{loss\_pct} = \max(0,\ \text{Win\%}_{\text{best}} - \text{Win\%}_{\text{played},S})
\]

3. 转折判断退化为单维胜率变化，无法计算三分量 \(S_{\text{turn}}\)，建议标记：

```text
turning_level = "降级判断"
```

并设置更高门槛，例如胜率下降 \(\ge 15\) 个百分点才标记为“可能决定性转折”。

---

## 11. 注意事项

1. **视角转换是最大坑**：`wdl` 和 `score cp` 均从 `side-to-move` 视角输出。走实际着法后轮到对手，必须转换回原走棋方视角。
2. **固定搜索条件**：对 \(F\) 和 \(F_P\) 使用相同深度或节点数，否则 `loss_pct` 可能包含搜索噪声。
3. **WDL 单位**：Stockfish 的 `wdl` 通常是千分比，例如 `wdl 145 780 75` 表示 `14.5% / 78.0% / 7.5%`。
4. **着法质量与转折独立输出**：一步 `Blunder` 不一定是转折；一步 `Inaccuracy` 也可能是决定性转折。
5. **特殊标签可关闭**：`Brilliant`、`Great`、`Miss` 是启发式规则，建议作为附加信息，不影响主分类。
6. **引擎版本影响 WDL 校准**：不同 Stockfish 版本、NNUE 网络和时间控制可能影响 WDL 数值，建议在工具中记录引擎版本和搜索参数。

---

**推荐落地顺序**：

1. 先实现 `UCI_ShowWDL` 解析与视角转换。
2. 实现 `loss_pct` + Lichess 四类分类。
3. 实现 \(S_{\text{turn}}\) + `delta_cp` 的转折分级。
4. 最后叠加 `Best`、`Brilliant`、`Great`、`Miss` 标签。
