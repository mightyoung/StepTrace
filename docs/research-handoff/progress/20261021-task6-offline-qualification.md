# Task6 下一步：离线资格包与 schema 反例

研究依赖保持 `ed9a9e403ad81408bb6bf3a47fac581429e6257e`；本增量从原任务分支 `8b9650d6c33d3e9062ca54a90a464270e67df803` 继续。实现提交 `9a0ec082cffac5254d30a3e1d041c2e3d9eb8ff2`，后续记录提交只追加本轮证据。

核对结果：本地/远端任务 head 一致、无用户未提交改动；未新增有效 rights attestation、实际 host/provider 或 backend runtime 记录，GitHub 仍只有历史交接草稿 PR、无开放 issue。已读 2026-10-14 权利审计、前轮资格计划和补证；未发现 AGENTS.md。用户批准继续推进没有提供第三方素材许可、接收方身份或后端实测事实，真实执行门仍关闭。

## 已执行的软件增量

[资格协议与最小私有字段](../../../experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/QUALIFICATION_PROTOCOL_zh.md)提供下一步具体材料要求。新增 `qualification-packet.template.json` 的 receiver 为 null，阶段为 not_run，不填写任何未知事实。backend 只是原固定 candidate，不代表用户已完成选择或验证。

新增 `tools/task6_qualification.py`：

- `audit --packet` 检查权利声明与 receiver 关联、媒体/衍生许可范围引用、manifest/contract hash、模型/后端/build/server 参数及四阶段同一 binary/provider/host。未知、占位、bool 冒充 exit code、失败或派发状态均不能算完整。
- 对未来 synthetic schema probe 的 metadata，要求 text-only、无私有媒体、F/HF 两次生成、无重试、完整响应 schema 校验及 server 停止声明。它不派发或实测探针；2048 tokens/60 秒等预算仍是未来协议、不是当前进程对后端的运行时约束。
- `schema-check --arm F|HF --content` 仅用固定原执行合同的 schema 校验命名 JSON；拒绝当前关系引用过去输入、F 引用历史、重复座位、额外字段和完成/机械锁/力状态越权结论。没有替换 frozen runner，也没有把 offline v2 schema 混进执行合同。
- JSON 重复 key、非有限常量、缺失文件和格式错误均阻断；错误报告不回显私有输入值。工具只读取显式指定的已授权 JSON 和固定公开 package JSON，不解析私有 evidence 引用、不读媒体/权重、不开网络/子进程。

即便完整 synthetic packet 通过，也只返回 `metadata_complete_pending_independent_review`。`evidence_files_verified=false`、权利和后端均 `not_independently_verified`、`execution_ready=false` 恒定。实际证据 hash/原件核查、真实主机归属、许可解释、后端行为仍需独立证据；不能用自填 hash 或字符串相同冒充实测。

## 实际验证

完整命令、exit、日志和 JUnit 身份、CLI 报告见[本轮验证记录](../receipts/task6-qualification-20261021/verification.json)。

- 最初新测试文件在工具不存在时：0 个方法运行、1 个 setup error（exit 5）。这不是已运行的 12 项测试。工具初版随后 12 tests OK。
- 后续探针约束按测试先行：13 项运行，10 个子测试断言失败（exit 1）；补上约束后 13 tests OK。没有把初始化失败或派发当作测试完成。
- 三个相关目录：**113 passed、107 subtests passed**，exit 0；新 Python 文件 ruff 和 diff 检查通过。
- 六项真实 CLI 软件 smoke：模板、缺失文件、重复 key、越权 SOP 内容 exit 2；合法 F/HF synthetic JSON exit 0。所有报告都保持零网络/零 generation、不可执行；[逐项命令与报告](../receipts/task6-qualification-20261021/cli-results.json)。
- 全仓根 pytest：**129 failed、880 passed、3 skipped**，exit 1。保存新的原样短摘要、完整两份数组、规范差集及 JUnit testcase 身份；129 个失败与此前保留基线一致，新增/消除均为空。原始 129 失败及前次补证没有改动；完整测试仍非全绿。
- 独立只读审阅者：self-hosted 目录 **65 passed、102 subtests passed**；lint/diff 通过；额外确认 frozen schemas 无远端 `$ref` 族，禁止 socket 创建后 audit/F/HF schema 校验仍通过。其 CLI 正反例也维持门禁，无阻断问题。未修改文件、联网或执行模型。

失败集合证据复算（不重跑测试/模型）：

```sh
python docs/research-handoff/receipts/task6-qualification-20261021/verify_evidence.py
```

脚本实际 exit 0；它核对[新摘要](../receipts/task6-qualification-20261021/root.summary.txt)、[两份数组/差集/JUnit testcase 字段](../receipts/task6-qualification-20261021/root-comparison.json)与前次公开基线，避免重犯旧回执把 ` - Fi...` 摘要后缀算作 node ID 的问题。完整 JUnit/traceback 只在原环境临时保留，hash 已记录；公开 testcase 字段是摘录，不能单凭它证明完整原始日志真实性。

## 仍待材料的具体行动

1. **媒体权利**：提供原视频/既有四 PNG 取得条款、视频和遮罩衍生帧的许可适用范围，以及和精确合同关联的有效私有 attestation/evidence。旧历史本机字节核验保留；不是再次找视频或重导出 PNG。
2. **接收环境**：填写 receiver.kind、provider_id、host_id、controller、terms_evidence_reference、retention_policy、training_use_policy、deletion_policy。建议先选择确实受控的自有主机；若托管则明确真实 provider/实例与数据条款，不把 loopback 当作没有第三方。
3. **固定后端资格**：确认是否沿用既有固定候选，并在具体主机、完整权限与有界资源授权具备后另行建立 build/runtime/无生成预检/独立 synthetic schema 生成证据。本次没有模型执行资格，不能用软件 schema pass 替代。

上述为待补事实/决策和未来执行权限，均没有审批拒绝或不确定发送。本次未读取/上传未授权媒体、未下载权重、未真实模型调用（包括 synthetic prompt）、未 GPU/主机 provision/付费/凭据配置、未扩大权限、未 AI 预标注/人工 GT/正式实验、未合入 main 或部署。
