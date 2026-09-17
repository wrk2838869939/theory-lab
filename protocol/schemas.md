# 协议与数据约定（所有 agent 与编排器共同遵守）· v2

## 1. 台账 state/ledger.json
```json
{
  "items": [
    {
      "id": "THM-1",
      "stage": "candidate | statement | lit-scan | sketch | rank | derive | verify | experiment | lit-check | integrate | done | refuted | blocked",
      "confidence": "white | yellow | red | green",
      "title": "...",
      "statement": "定理陈述（尽量形式化）",
      "refs": "文献线索（上下文材料编号）",
      "tournament": false,
      "n_candidates": 3,
      "angles": ["锦标赛各草图的技术视角（仅 tournament 项需要）"],
      "repair_rounds": 0,
      "review_round": 0,
      "statement_round": 0,
      "novelty_status": "CLEAR | CONFLICT | UNKNOWN | null",
      "notes": "人工与系统备注",
      "history": [ {"time": "...", "phase": "...", "marker": "...", "artifact": "...", "note": "..."} ]
    }
  ],
  "meta": { "paper_reviews": 0, "paper_gate": null }
}
```

## 2. 状态机 v2（由编排器执行，agent 不得自行改台账）
```
candidate => statement --PASS--> lit-scan --CLEAR--> [sketch -> rank ->]? derive -> verify
             ^ Revise: 自动套用<revised-statement>  |CONFLICT-> blocked(red)      |SOUND-> experiment
             | 并复审（<=2 轮，超限 blocked）                                      |--GAP/UNSOUND-->(repair<=4)-> derive
             `--------------------------------------------------------------------|--REFUTED-> refuted(red)
                                                     experiment --CONSISTENT--> lit-check --CLEAR--> integrate --UPDATED--> done(green)
                                                                 |CONFLICT-> blocked(red)  |UNKNOWN-> blocked(yellow, 文献待核)
                                                     (MISMATCH -> red, 回 derive)
```
- sketch/rank 仅对 `"tournament": true` 的定理启用：T1 按 N 个技术视角各出一份证明草图 → T2 锦标赛排名（WINNER: k）→ 只对胜者 derive。
- lit-scan（前置，防撞车）与 lit-check（收口，核实证明中 [LIT] 引用并复核新颖性）都由 R1 执行。
- interactive 模式（默认）：任一 blocked / refuted 编排器整体停机，转人工门。
- autonomous 模式（`--mode autonomous` 或 `lab.py auto`）：blocked/refuted 条目留置（parked，
  不销毁、不跳证据门），流水线继续推进其余任务直到全部终态，收尾时汇总待人工跟进条目；
  预算/证据门/轮次上限等安全边界不变。`--thm --phase` 定向操作在两种模式下都可绕过整体停机。
- 以当前 orchestrator.finalize 与 host-evidence.md 为准：lit-check 的 UNKNOWN 不进入成文；
  CLEAR 也必须具备当前证明/陈述绑定的审稿、实际执行和实际检索证据。
  宿主如需依次推进多个候选至 lit-check，可在保留前项阻塞状态的同时处理下一候选。
  完成这一有限阶段任务不等于台账 done，也不等于论文完成。
- 全部定理 done 后：T2 对整篇论文做 paper_review；REVISE 生成 PAPER-FIX 任务；PASS 后进入最终人工门。

## 3. 四色置信度（P0-1，编排器自动维护）
| 颜色 | 含义 | 触发 |
|---|---|---|
| white | 未经过独立审查 | candidate / 刚进入流水线 |
| yellow | 在审或部分通过 | verify SOUND 之后、修复循环中、novelty UNKNOWN |
| red | 存在未解决的严重问题 | UNSOUND / REFUTED / MISMATCH / novelty CONFLICT |
| green | 三重验证闭环 | integrate 完成 且 novelty CLEAR |

## 4. 产物与最后一行标记（机器解析契约）
| 阶段 | 产物文件 | 最后一行标记 |
|---|---|---|
| statement | reviews/{id}-statement-r{轮}.md | `FIDELITY: PASS\|REVISE`（REVISE 须含 `<revised-statement>` 块） |
| lit-scan | literature/{id}-scan.md | `NOVELTY: CLEAR\|CONFLICT\|UNKNOWN` |
| sketch | sketches/{id}-k{i}.md（N 份） | `STATUS: READY\|BLOCKED` |
| rank | reviews/{id}-ranking.md | `WINNER: k` |
| derive | derivations/{id}.md | `STATUS: PROVED\|PARTIAL\|BLOCKED` |
| verify | reviews/{id}-r{轮}.md | `VERDICT: SOUND\|GAP\|UNSOUND\|REFUTED` |
| experiment | experiments/{id}/report.md | `EXPERIMENT: CONSISTENT\|MISMATCH\|NOT-APPLICABLE` |
| lit-check | literature/{id}-check.md | `NOVELTY: CLEAR\|CONFLICT\|UNKNOWN` |
| integrate | paper/paper.tex | `STATUS: UPDATED\|BLOCKED` |
| paper_review | reviews/paper-r{轮}.md | `PAPER: PASS\|REVISE` |

## 5. 事件日志与审计（P1-6）
- state/events.jsonl：追加式 JSONL（promote/call/response/transition/error），全量审计线。
- state/prompts/ 与 state/responses/：每次调用的完整提示词与原始回复（不入 git）。
- `--audit` 命令生成 state/audit-report.md（调用统计 + 逐定理轨迹）与
  paper/verification-appendix.md（投稿随附的三重验证轨迹汇总，人工校订后使用）。

## 6. 上下文边界
- agent 之间**不直接对话**，只通过产物文件交流；编排器在每轮把相关材料注入提示词（单文件截断阈值 policy.max_chars_per_artifact）。
- state/goal.md 每轮注入所有 agent：目标、完成定义、约束、认知校准。
- config.json 的 `context_files`（`[{"path": ..., "label": ...}]`）声明项目级背景材料
  （如选题综述、种子文献清单），按需注入 statement / lit-scan / paper_review 等阶段。

## 7. 外部调用预算（state/budget.json）
- `max_calls`：批准的外部调用总次数；`max_output_tokens_per_call`：单次输出上限。
- 每次请求前先预留（失败/超时也计数），重启不重置；`reservations` 逐条留痕。
- provider-events.jsonl 记录每次请求的状态与供应商返回的使用量。
