# 宿主核验证据接口

普通 API 回复没有真实检索或代码执行能力。模型声称 `CONSISTENT`、`NOT-APPLICABLE`、`CLEAR` 或 `verified` 不能生成已核验状态。默认实验阶段的这类回复记为 `UNEXECUTED`；缺少宿主检索记录的 `CLEAR` 降为 `UNKNOWN`；收口核查不完整时停机，禁止成文。

提供两种导入方式：

1. Python 接口 `orchestrator.finalize(item, phase, report, cfg, start_ts, evidence=receipt, ledger=ledger)`；
2. 命令行 `python host_pipeline.py <定理ID> <阶段> <报告文件> --evidence <核验记录文件>`。

两者都只是**受信宿主的导入接口**，不是证据生成器或模型工具。调用前宿主必须实际完成执行/检索、检查报告与记录一致。禁止从模型回复中提取一段"证据 JSON"直接传入。

## 收据字段

| 字段 | 含义 |
|---|---|
| `kind` | 实验使用 `execution`；文献预扫描/收口使用 `retrieval`。 |
| `report_sha256` | 本次传给 `finalize` 的完整报告字符串，按 UTF-8 编码计算 SHA-256；包括末行标记。 |
| `proof_sha256` | 当前 `derivations/{定理ID}.md` 经 UTF-8 读取后的字符串哈希；预扫描尚无证明时为 `None`。 |
| `statement_sha256` | 当前台账 `item['statement']` 字符串的 UTF-8 哈希。 |
| `artifact` | 真实宿主核验记录的工作区相对文件路径；文件必须存在且非空，解析后的路径必须位于工作区内。 |
| `sha256` | 上述核验记录文件的原始字节 SHA-256。 |

实验记录应保存实际命令、执行环境、代码版本、随机种子、退出状态、输出及对照检查；检索记录应保存查询时间、实际查询、访问的原始来源，以及逐条论断的原文支持位置和未解决项目。宿主必须独立检查这些内容。即使实验不适用，也必须由宿主审查适用性并记录理由，不能接受模型单方面免责。

**哈希只绑定版本与防止误用过期材料，不证明记录内容真实，也不证明数学正确性或全球新颖性。** 此接口以调用方宿主为信任边界；拥有工作区写权限的程序可以伪造文件与哈希。因此不要把文件完整性检查描述为事实核验、形式化证明或防恶意进程的安全隔离。

## 宿主使用示例（Python）

下例只导入宿主已经真实执行并审核过的实验报告与日志，不会执行实验。两个文件应事先由可信执行器生成并经核对；不要创建占位日志来通过证据门。实际定理 ID 可按台账替换。

```python
import hashlib
import time
import orchestrator as o

cfg = o.load(o.ROOT / 'config.json')
ledger = o.load(o.STATE / 'ledger.json')
item = next(x for x in ledger['items'] if x['id'] == 'THM-1')

# 可信执行器已真实运行，并核对该完整报告与执行日志一致。
report_path = o.ROOT / 'experiments/THM-1/report.md'
log_path = o.ROOT / 'experiments/THM-1/host-execution.json'
report = report_path.read_text(encoding='utf-8')
assert o.parse_marker(report, 'EXPERIMENT') == 'CONSISTENT'
receipt = {
    'kind': 'execution',
    'report_sha256': o.digest(report),
    'proof_sha256': o.proof_digest(item),
    'statement_sha256': o.digest(item['statement']),
    'artifact': str(log_path.relative_to(o.ROOT)),
    'sha256': hashlib.sha256(log_path.read_bytes()).hexdigest(),
}
marker, changed = o.finalize(
    item, 'experiment', report, cfg, time.time(),
    evidence=receipt, ledger=ledger,
)
o.save(o.STATE / 'ledger.json', ledger)
```

等价的命令行用法（先真实执行并核对报告与日志）：

```bash
python3 host_pipeline.py THM-1 experiment experiments/THM-1/report.md \
    --evidence experiments/THM-1/host-execution.json
```

收口文献核验使用同一模式，阶段改为 `lit-check`、`kind` 改为 `retrieval`，读取已经完成真实检索并逐条核对的文献报告和宿主检索记录。该报告需有有效末行 `NOVELTY: CLEAR`，且证明、对抗审稿、实际实验均已有当前版本的完整核验轨迹，才能进入 `integrate`。

续跑时应先导入实验核验，再导入文献收口核验；重新导入实验会使已有文献收口检查失效，需要重新核验。修改证明或陈述也会使旧核验链失效。每次回复会另存到 `state/artifacts/`，完整证明、审稿、实验、文献及宿主记录共同构成成文输入；单独设置台账 `confidence=green` 不会通过检查。

`archived` 和 `archived-proposal` 条目只保留历史，不参与当前论文完成门。自动 Git 提交默认关闭；只有配置明确设置 `policy.auto_commit: true` 才会调用已有全仓暂存与提交逻辑。

辅助工具：`run_host_checks.py <输出文件> <脚本...>` 以固定环境运行宿主审阅过的本地检查脚本并保存带哈希的执行记录，可直接作为 `execution` 收据的 `artifact`。
