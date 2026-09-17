# Theory Lab：可审计的多智能体闭环科研流水线

一个以**黑板架构 + 确定性状态机**组织的多智能体科研自动化框架：多个异构模型 agent 围绕
共享文件仓库分工迭代，由编排器驱动"陈述 → 文献 → 证明 → 对抗审稿 → 实验 → 文献收口 → 成文"
的闭环，目标是产出经过多重核验的理论结果。

框架本身**不包含任何具体研究内容**：研究领域由 `state/goal.md`、台账
（`state/ledger.json`）与 `config.json` 的 `context_files` 提供。
仅依赖 Python 标准库，无第三方包。

核心设计立场：**模型自报结果不是证据。** API 对话没有真实的检索与代码执行能力，
因此一切"已验证"状态都必须由宿主（人或受信桥接程序）提供执行/检索收据；
预算、事件、提示词与回复全量留痕，可审计。

## 架构总览

```
                        ┌──────────────────────────────────────────┐
                        │        orchestrator.py（确定性状态机）      │
                        │   组装提示词 / 调用 agent / 解析标记 / 转移状态  │
                        └───────┬─────────┬─────────┬─────────┬─────┘
                                │         │         │         │
                     statement  │lit-scan │  verify  │experiment│integrate
                     (陈述门)    │(防撞车)  │ (对抗审) │(结构检查栈)│ (成文)
                                ▼         ▼         ▼         ▼
                        ┌───────────┐┌───────────┐┌───────────┐┌───────────┐
                        │ T1 主证明者 ││ T2 对抗审稿 ││ R1 文献侦察 ││ R2 实验/成文│
                        │ (可插拔)   ││ (可插拔)   ││(可插拔)    ││ (可插拔)   │
                        └─────┬─────┘└─────┬─────┘└─────┬─────┘└─────┬─────┘
                              │            │            │            │
        ══════════════════════ 黑板：共享文件仓库（events.jsonl 提供全量审计）═════════════════════
          state/goal.md   state/ledger.json   derivations/   reviews/   sketches/
          literature/     experiments/        paper/paper.tex   state/events.jsonl
```

关键设计：**agent 之间不直接对话**，一切通过产物文件交流。这样上下文有界
（编排器按阶段注入材料）、过程可审计（events.jsonl + prompts/responses 留痕）、
异构运行时可插拔（API 或任意 CLI agent，见 config.json 的 `backend` 字段）。

## 单定理流水线与状态机

```
常规:     statement -> lit-scan -> derive -> verify -> experiment -> lit-check -> integrate
锦标赛项: statement -> lit-scan -> sketch(N份) -> rank -> derive -> ...
```

- verify 发现缺口退回 derive（≤ `policy.max_repair_rounds` 轮）；陈述 REVISE 自动套用
  `<revised-statement>` 并复审（≤ `policy.max_statement_rounds` 轮）。
- 任一 blocked / refuted：编排器停机，转人工门。
- 全部定理 done 后 T2 对整篇论文做 paper_review；REVISE 生成 PAPER-FIX 任务；
  PASS 后进入最终人工门（人工终审，系统不自动投稿）。

四色置信度随状态机自动更新：white=未独立审查 / yellow=在审或部分通过 /
red=存在未解决严重问题 / green=三重验证闭环。完整字段与标记契约见
[protocol/schemas.md](protocol/schemas.md)。

## 角色（protocol/）

| Agent | 职责 | 阶段 |
|---|---|---|
| T1 主证明者 | 锦标赛草图 + 写/修证明（编号步骤、[LIT]/[CALC]/[NEW]、GAP 显式化） | sketch, derive |
| T2 对抗审稿人 | 陈述保真度门、证明审查（小实例反例/攻击记录）、锦标赛排名、全文审 | statement, rank, verify, paper_review |
| R1 文献侦察 | 前置扫描（防撞车）与收口核查（引用核实 + 新颖性复核） | lit-scan, lit-check |
| R2 实验工程师/执笔 | 数值实验（结构检查栈）与论文成文 | experiment, integrate |

模型、端点与角色绑定全部在 `config.json` 中配置，可随时更换供应商。
`provider_client.py` 把每个 API 密钥钉死在对应的官方端点上（密钥串不会发给其他域名）。

## 快速开始

```bash
git clone <本仓库> && cd theory-lab

# 1) 配置模型：编辑 config.json，把 REPLACE_WITH_YOUR_MODEL 换成你的模型 id
#    （可用 python lab.py check 查询账号可用模型）
# 2) 设置预算上限（默认 20 次调用、单次 8192 输出 token）
# 3) 填写 state/goal.md（研究目标、纪律、停机条件）
# 4) 在 state/ledger.json 登记候选定理（格式见 protocol/schemas.md）

export OPENAI_API_KEY=...      # 或 ZHIPU_API_KEY / MOONSHOT_API_KEY 等
python lab.py status           # 台账 + 预算
python lab.py verify           # 本地回归测试（不消耗预算）
python lab.py check            # 查询供应商可用模型（计一次预算）
python lab.py pilot            # 两模型独立推导 + 交叉审稿（消耗预算）
python lab.py pipeline --steps 4   # 按台账推进 4 个阶段（interactive 模式）
python lab.py auto                  # 自主模式（实验）：推进到全部任务终态（见下节）
python orchestrator.py --dry-run   # 只渲染提示词，不调用、不改状态
python orchestrator.py --audit     # 生成审计报告与验证附录
```

试点任务文本放在 `research/pilot-task.md`（模板见 `research/pilot-task.example.md`）。
Windows 可用 `start-research.cmd <mode>` 入口。

## 两种运行模式

> **推荐使用 interactive（默认）。** autonomous 为**实验功能**：机械行为已由测试覆盖
> （见 tests/test_state_machine.py 的 RunModeTests），但长时无人监督运行的行为尚未在
> 大规模真实项目中验证，请自行评估后再用于无人值守场景。

| 模式 | 状态 | 停机行为 | 适用 |
|---|---|---|---|
| **interactive**（默认） | ✅ 推荐 | 任一 blocked/refuted 立即**整体停机**转人工门；`--auto` 受 `policy.max_steps_per_run` 步数上限约束 | 有人监督的分段推进 |
| **autonomous** | 🧪 实验 | 宿主协调者接管停机决策：blocked/refuted 项**留置**（不销毁、不跳过证据门），流水线继续处理其余任务，直到全部任务到达终态才结束；`--auto` 不设步数上限 | 完全脱离的批量运行（研究方向决策由宿主协调者控制） |

```bash
python orchestrator.py --auto --mode autonomous   # 等价：python lab.py auto
python orchestrator.py --steps 4 --mode autonomous  # 也可有界推进
```

自主模式解除的只是**决策性停机**，不是安全边界：调用预算（耗尽即停、失败也计数）、
证据门（无宿主收据不成文）、修复/复审/全文审查轮次上限、输出截断保护全部照常生效；
运行结束时会汇总留置待人工跟进的条目与论文门状态。默认模式可在
 `config.json` 的 `policy.default_mode` 中调整。

### 版本

- **v0.2.0**：两种运行模式（interactive / autonomous-实验）；根目录工具路径修复；
  非交互环境密钥提示修复。
- **v0.1.0**：初始发布（黑板架构编排器、预算化供应商客户端、宿主证据收据、
  对抗审稿角色与离线回归测试）。

## 预算与证据模型

- **预算**（`state/budget.json`）：每次外部调用前先预留，失败与超时也计数，重启不重置；
  单次输出 token 上限受预算文件约束，超限请求直接拒绝。
- **证据**（[protocol/host-evidence.md](protocol/host-evidence.md)）：experiment / lit-check
  阶段要进入"已验证"状态，必须由宿主通过 `orchestrator.finalize(..., evidence=receipt)`
  或 `python host_pipeline.py <id> <phase> <report> --evidence <log>` 导入真实执行/检索收据。
  收据把报告、证明稿、陈述与核验日志的 SHA-256 绑定在一起；修改任一文件都会使旧证据失效。
- 模型自报 `CONSISTENT` / `CLEAR` / `SOUND` 不能代替执行和检索：无宿主记录的实验记
  `UNEXECUTED`，无检索记录的 CLEAR 降为 `UNKNOWN`；证据链不完整时禁止成文。
- `run_host_checks.py <输出> <脚本...>` 以固定环境运行宿主审阅过的检查脚本并保存
  带哈希的执行记录，可作为 `execution` 收据。

## 文件与状态

| 文件/目录 | 用途 |
|---|---|
| config.json | 模型、角色绑定、阶段映射、策略；**不含密钥** |
| provider_client.py | 官方端点绑定、流式接收、预算与使用量记录、密钥脱敏 |
| orchestrator.py | 状态机编排：提示词组装、标记解析、状态转移、审计 |
| run_research.py | 试点轮：两模型独立推导 + 交叉审稿（含 pipeline 入口） |
| host_pipeline.py | 宿主收据导入 CLI（experiment/lit-scan/lit-check） |
| run_host_checks.py | 运行指定本地检查并保存带哈希的执行记录 |
| review_theorem.py | 对 verify 阶段的定理发起一次预算内外部审稿 |
| lab.py / start-research.cmd | 便携入口（status/check/pilot/pipeline/verify） |
| AGENTS.md | 主 agent（宿主协调者）职责、健康监控清单与硬性禁令 |
| protocol/ | 角色提示词、数据契约、宿主证据接口说明 |
| tests/ | 离线回归测试（状态机、预算、脱敏、证据绑定） |
| state/ledger.json | 定理候选与进展台账（四色置信度 + 全量历史） |
| state/budget.json | 持久调用预算（失败也计数，重启不重置） |
| state/goal.md | 项目目标与纪律（每轮注入所有 agent） |
| state/events.jsonl · provider-events.jsonl | 事件流与供应商调用留痕 |
| state/prompts/ · state/responses/ | 每次调用的完整提示词与原始回复（不入 git） |
| state/artifacts/ · state/verification/ · state/incomplete/ | 不可覆盖快照、核验证据、截断诊断 |
| derivations/ reviews/ sketches/ literature/ experiments/ paper/ | 各阶段产物（黑板） |
| research/ | 项目自备材料：试点任务、综述、种子文献等 |

## 安全与隐私

- API 密钥只从环境变量读取，不写入任何文件；错误信息与日志经过脱敏（`redact`）。
- 凭证-端点钉死：`provider_client.ALLOWED_BASES` 限定每个密钥只能发往对应官方端点，
  拒绝重定向，防止凭证被转发到第三方域名。
- 不自动执行模型生成的代码；模型输出的实验代码由宿主审阅后手动执行。
- `--dry-run` 只渲染提示词；`policy.auto_commit` 默认关闭。

## 局限与免责声明

- 状态标记是机器读取的流程契约，不是数学正确性的证明；最终正确性仍需人工读证。
- 新颖性默认 UNKNOWN，须由宿主真实检索后才能升级；系统不自动发布或投稿任何成果。
- 哈希绑定版本，不证明内容真实；证据接口以调用方宿主为信任边界。
- 本项目不承诺任何具体研究结果；请遵守所用模型供应商的服务条款。

## 许可证

[MIT](LICENSE)
