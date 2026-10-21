# Task6 失败集合比较：追加证据与更正

本记录追加到 `25c6717a0736cb5358bc3e2dd27c081383ca1464`，不覆盖历史报告或回执，不修改准入实现，不重跑软件测试、模型或正式实验。

旧[回执](../roboassem-task6-rights-evidence-binding-20261021.json)仅保存一份 `failed_test_nodes`，并给出空的新失败/已消除列表。其中 `tests/test_research_contract.py::test_task_id_column_is_mandatory - Fi...` 包含 pytest 异常摘要后缀，所以旧字段不是严格的完整 node ID 数组。旧程序比较的是两轮原样 `FAILED ` 摘要行集合；将其表述为已交付可独立复算的 node ID 集合证据不够准确。

## 原日志恢复情况

两份原始文本日志仍在原保存云环境的 `/tmp`，均逐字节匹配历史回执记录的大小与 SHA-256：

| 运行 | 原日志文件 | bytes | SHA-256 |
|---|---|---:|---|
| 原始基线 | `steptrace-baseline-pytest.log` | 265685 | `1a6f5f41f9c7ff4e09e13152e1dc02c48e83709343eefc70e4ea97d39ff982de` |
| 修复后工作树 | `steptrace-full-pytest.log` | 263067 | `533975198f4620d687b8a54fbbbb0316f34fbf68ffcfc9d7ed5068bf779f21b2` |

两轮命令均为 `/tmp/steptrace-check-venv/bin/python -B -m pytest -q`，exit 1，129 failed、880 passed、3 skipped。基线在 `ed9a9e403ad81408bb6bf3a47fac581429e6257e` 干净归档内运行；修复后是提交前的工作树，其实现随后保存为 `04a68c13208ad9c5d9a37490e0c434db9777b53a`。这不是在最终回执提交上新增的一轮执行。

完整 traceback 日志继续只在原环境临时保留，未公开；本目录仅公开既有公开测试的短摘要原样摘录及派生结果，不含媒体、模型输出、凭据或私有输入。

## 可复算材料

- [baseline.summary.txt](baseline.summary.txt)、[after.summary.txt](after.summary.txt)：分别来自原日志的 `short test summary info` 标题至原文件末尾，未重新构造、清洗或改写摘要行。
- [provenance.json](provenance.json)：原始两轮命令、目录、exit、日志身份及摘录的原始行范围。完整原日志 hash 用于原环境核对；公开摘录本身不能证明完整日志的全部内容或真实性。
- [comparison.json](comparison.json)：分别包含 baseline/after 的 129 项规范完整 `failed_node_ids` 数组、各摘录 hash、移除后缀的逐条记录，以及 `new_failed_node_ids`、`resolved_failed_node_ids`。
- [recompute.py](recompute.py)：仅用标准库从两份摘录重新计算，不联网、不运行测试/模型、不改写文件。

解析保留带空格的参数化 node ID，不按空白拆分。只删除 pytest 的 ` - <exception summary>` 后缀；两轮各仅一条存在该后缀，均为上述非参数化函数，移除内容均为 ` - Fi...`。不存在截断的 node ID，也没有根据源码补造缺失 node ID；脚本遇到 node ID 含 `...` 或 `…` 会拒绝。保留的参数化方括号内容均原样来自日志。

在仓库根目录复算：

```sh
python docs/research-handoff/receipts/task6-failure-comparison-20261021/recompute.py > /tmp/steptrace-recomputed-comparison.json
cmp docs/research-handoff/receipts/task6-failure-comparison-20261021/comparison.json /tmp/steptrace-recomputed-comparison.json
```

上述两条命令实际均 exit 0。规范结果为 baseline 129 项、after 129 项，均无重复；新增失败 `[]`、消除失败 `[]`、`equal_failed_node_sets=true`。现在可以从已保存的两份公开摘要独立复算规范失败集合相等；这只说明这两轮既有软件测试的失败身份一致，不代表全仓通过、失败原因逐项一致、无任何潜在回归或研究结果成立。

本次首次创建材料的 shell 调用曾出现 exec-server transport disconnected。随后检查目录不存在、工作树无新增文件，确认未执行后才重新创建；没有权限拒绝或不确定提交/推送。原报告与旧回执均保留，后续使用本补充的规范数组与差集。

## 独立只读复核

另一内部审阅者核对了原日志 hash、两份第 5011–5141 行的原样摘录、各 129 个唯一 ID（各 36 个参数化 ID）及差集；`python3 -B …/recompute.py | cmp - …/comparison.json` 实际 exit 0，独立 Python 行切片/身份/括号/集合检查也通过，无阻断问题。审阅者首次用整字典相等核对旧 identity 时因旧记录含额外 `retention` 字段而断言失败；改为精确比较 bytes/SHA-256 后两份均通过，未修改证据。审阅未重跑测试/模型、未联网或改文件。

解析器仅针对这两份冻结摘录；`partition(" - ")` 不保证能解析任意将该字串用作参数 ID 的 pytest 输出，本次已逐项核实唯一匹配是非参数化测试的摘要后缀。
