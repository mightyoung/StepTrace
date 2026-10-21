# Task6 权利证据绑定与后端资格计划 — 2026-10-21

本次研究基线为 `ed9a9e403ad81408bb6bf3a47fac581429e6257e`，不是默认 main 的发布快照。远端任务分支已读，开放 issue 为零，唯一开放 PR 是历史交接草稿 #1；未发现 AGENTS.md。原任务目标仍是单人视频中依可见证据识别步骤、保留 unknown、限制历史证据越界。Task6 是独立开发候选，F/HF 一次性对照尚未运行；本增量只推进其未来执行前的准入。

## 可复核增量

原 CPU preflight 会记录 evidence_file 当前的实际 hash，却未要求它等于 rights attestation 固定的预期身份。同长度替换的 synthetic 文本仍得到 data_rights=verified、input_ready=true。本次让声明包含 `evidence_bytes`（正整数，排除 bool）和 `evidence_sha256`（64 位小写十六进制），并比较实际文件字节数与 hash。缺项旧声明、错误类型、篡改证据都会阻断；原记录和冻结 runner 未改写。

另一个反例是 CLI provider 与 attestation 同为 UNSET、空值或泛称“自托管”时仍相等。本次拒绝这些占位值；不声称可自动识别任意含糊名称，也不声称已将 provider 字符串绑定到真实硬件或第三方账户。

保持 v1 schema 名称，新增两项必填的准入约束。模板默认 null/空 digest，仍为未授权。已有 v1 私有记录需要由操作者重新审阅固定证据身份；不能自动计算并填入后就宣称合法。修改私有权利证据内容后，声明也需重新审核。哈希绑定不是签名验证，不证明许可文本真实性或权利范围；同时修改声明和证据的伪造不在此机械检查能力内。

## 权利与接收边界表

| 对象或动作 | 已有证据 | 未决范围 / 未来所需材料 |
|---|---|---|
| 原视频与 k01–k04 | 历史 Mac 已核验精确字节身份；本云环境未重读；原文件不是缺失研究素材 | 获授权私有执行位置及原取得条款；不得重新导出 PNG 代替原件 |
| 媒体许可 | 2026-10-14 审计记录网站 MIT 与 CC BY-SA 声明 | 哪项适用于该视频、既有遮罩帧与衍生链；需第一方材料澄清，不能从模型许可推导 |
| 自有机器 + loopback | loopback 是本机 endpoint，不表示调用 OpenAI 等外部模型 API | 主机所有/控制方、稳定 host ID、复制与推理范围，保留私有证据 |
| 租用机器 + loopback | endpoint 本地仍不排除托管方接收数据 | 实际 provider、租户/实例边界、数据用途/保留/训练/删除条款和相关授权 |
| 外部模型 API | 当前固定方案不使用此路径 | 新接收者及新的传输/用途审核；不能沿用自有主机授权 |
| 模型使用与再分发 | 固定 Unsloth/Qwen 许可链在旧审计中可查 | 真正使用时核对精确权重 hash；再分发另需适用通知和用户授权，不授予媒体权利 |
| 公开图片/输出 | 运行包默认禁止公开再分发 | 按具体材料核对署名、修改标记、衍生/ShareAlike 等适用义务；本次不作法律裁定 |

证据依据：[来源与权利审计](../audits/roboassem-task6-source-rights-audit-20261014.md)。本次没有重新联网解释许可，没有获取媒体或权重。`expires_utc` 是准入声明的复核有效期；许可自身期限保持其来源表述，未知就记录未知。

## 固定后端资格验证方案（未执行）

| 阶段 | 未来验收证据 / 拒绝条件 | 可作何种结论 |
|---|---|---|
| 权利与接收 | 精确合同、视频/PNG 身份、证据 hash、具体 host/provider、实际授权用途一致；许可未决或接收不明则停止 | 机械准入与人工范围审阅齐备，不代表实际运行 |
| 构建身份 | llama.cpp commit `8345f333951c661d166b00e6f9362e553768f292`、固定 build flags、binary bytes/hash、完整构建终态和 exit；失败不换浮动版本 | 指定主机的构建记录，不能推断推理成功 |
| runtime | 已有 record-runtime 捕获 Python/CUDA/driver/显存/RAM/磁盘、版本/help；与 manifest 比较 | 候选 runtime 匹配；主机与 provider 仍需人工核对 |
| 无生成 GPU 预检 | 获额外执行授权后才启动固定 server；保留 props、F/HF token 上界、停止回执；不发 generation | 仅能证明加载与 endpoint/token 检查；不能证明 strict schema generation 或视觉质量 |
| strict schema 资格 | 未来需单独批准不含私有数据的最小生成探针，固定同一 binary/模型/参数并验证实际完整输出、失败及停止；不能只凭参数存在判定支持 | 后端能力资格，不消耗/冒充正式 F/HF 配对，也不计为真实样本或人工真值 |
| Task6 配对 | 上述审阅通过后另行授权；F/HF 独立上下文、同一 k04、最多各一次、无重试；保存原始字节及完整账本 | AI 预标注/视觉估计；无独立盲标人工参考就不计准确率/方法增益 |
| 正式研究 | 冻结协议、独立样本、未污染 split、合格独立人工真值、固定 backend 与可复现参数 | 才能按预注册范围进行正式实验；本开发小试不升级为正式证据 |

固定后端尚未合格。当前无主机、无构建产物、无严格 schema 生成证据；本次未运行 GPU 预检、任何真实或 synthetic 模型推理。

## 最小具体决策包

1. 指定私有原件的获授权执行位置（不需把媒体发给本线程），确认沿用既有精确视频/四 PNG；提供其取得条款与许可适用范围材料。
2. 选择具体接收边界：自有主机的稳定 host ID/控制者，或托管方及实例边界；明确传输、推理、保留/删除范围。不要只答“自托管”。
3. 对未来固定后端资格工作另给有界资源/时间授权，并决定是否批准仅 synthetic、无私有数据的 schema 资格探针；当前不购买、不 provision、不连接或配置凭据。

上述决定仍待材料；所有不依赖决定的本次软件工作已独立推进。后续证据交付无需以“原视频没找到”为阻塞理由。

## 实际验证与独立复审

实现提交：`04a68c13208ad9c5d9a37490e0c434db9777b53a`；本报告/回执可能位于其后续记录提交，研究基线与依赖未变。完整命令、exit、日志哈希及全部 129 个失败 node ID 见[追加回执](../receipts/roboassem-task6-rights-evidence-binding-20261021.json)。

- 测试先行：首次 pytest 因环境没有模块而未执行；改用 unittest 后，修改前 7 个方法出现 17 个预期断言失败（含子测试），修改后 7 项通过。
- Task6 self-hosted 52 项 unittest、offline runner 24 项通过；v5 crash safety 在 `python -O` 下 24 项通过。三个目录合并 pytest：100 passed、66 subtests passed。
- 根目录完整 pytest：129 failed、880 passed、3 skipped。对原始 `ed9a9e4` 干净归档运行同一命令得到相同计数和完全相同的 129 个失败 node ID；未新增失败，但现有失败没有解决，不能声称全仓通过。根 pytest 的 testpaths 只收集 tests/，所以相关 experiments/ 另行显式运行。
- Ruff 在两个已有 Python 文件中报 4 个 F841，逐一对基线证实已有；新增测试文件 lint 通过。`git diff --check` 通过。
- 针对空临时输入目录的真实 CLI CPU smoke 返回 2，`input_ready=false`、`execution_ready=false`、网络和 generation 计数均为 0；这只是软件阻断验证，不表示既有私有原件不存在。
- 初始环境缺 pytest/OpenCV；在 `/tmp/steptrace-check-venv` 隔离安装测试依赖（pytest/ruff/OpenCV 等），未改全局依赖、未下载模型或媒体。
- 另一个内部只读审阅者独立检查 diff、重跑 52 项 unittest 与 52 项 pytest（61 subtests），确认无阻断问题，且复核 4 个 lint 问题来自基线。其未修改代码、联网或联系外部人员。

未执行：GPU 构建/加载/预检、真实或 synthetic 模型生成、媒体读取/上传、正式实验、AI 预标注、人工真值、provider 配置/购买、凭据配置、合入 main、部署。剩余阻塞是待补权利范围与接收环境证据、待合格固定后端；没有自动审批拒绝或不确定已发送请求。
