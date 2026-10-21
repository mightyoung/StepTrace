# Task6 固定后端资格记录与离线验收

状态仍为 `preparation_only_blocked_for_execution`。本协议将前一轮资格计划变成可填写的私有记录和可执行的离线字段检查；不选择 provider，不获得媒体许可，不配置主机，也不运行任何生成。用户批准继续软件工作没有补齐这些事实。

## 本次可运行的离线工具

`qualification-packet.template.json` 复制到操作者自己的获授权私有位置后填写。未知字段保持 null；阶段保持 not_run，不能把计划或派发写成 completed。仓库模板不是授权或已完成证据。

```sh
python experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/tools/task6_qualification.py audit \
  --packet /path/to/private/qualification-packet.json
```

工具只读取所指定的 JSON 与 package 固定公开 manifest；不会解析 evidence_reference 指向的内容，不读媒体/权重、不查 provider、不执行 build/server/模型、不联网。调用前仍需确认该 JSON 本身已获读取授权；不要把包含私有标识的包提交仓库。报告只列字段问题，不回显字段值。重复 JSON key 或 NaN/Infinity 会阻断。

exit 0 仅表示 `metadata_complete=true`、`status=metadata_complete_pending_independent_review`；工具始终输出 `evidence_files_verified=false`、`rights_verification=not_independently_verified`、`backend_qualification=not_independently_verified`、`execution_ready=false`。exit 2 表示缺项、不同步或无效输入。主机/证据 SHA 在这里是声明而非本机实测；旧 `task6_bundle.py preflight` 的实际文件 hash 检查仍须在获授权私有执行位置另行运行，不被此工具替代。

## 最小需用户提供的字段

| 字段 | 具体所需值与建议 |
|---|---|
| receiver.kind | 自有自管机器选 self_managed；第三方托管 VM 选 cloud_host。外部模型 API 不属于当前 loopback 方案，需另审接收路径 |
| receiver.provider_id / host_id / controller | 实际接收方、稳定私有机器/实例标识、拥有或控制该机器的主体；不要填“自托管”或 unknown。无需公开这些值 |
| receiver.terms_evidence_reference | 归属/控制边界或托管条款的私有记录引用 |
| receiver.retention_policy / training_use_policy / deletion_policy | 明确输入/输出保留期限、是否用于训练、删除责任与时间；未知保持 null，不能默认“不训练” |
| rights.media_scope_evidence_reference | 能说明原视频及取得来源适用许可范围的第一方材料；2026-10-14 审计的网页级声明仍未解决该范围 |
| rights.derivatives_scope_evidence_reference | 覆盖既有 k01–k04 遮罩 PNG 衍生/传输/研究推理的材料；无需重新制作 PNG |
| rights.attestation | 现有精确合同、接收方、授权动作、证据大小/hash、有效复核时间完整声明；凭据未知不得写 true |
| backend | 模板只是既有 Qwen/llama.cpp 候选快照，不代表用户已选定或本机验证。建议维持固定 candidate 做资格核对；改变模型/后端/参数须另版合同，不能静默覆盖 |

用户只需先补接收边界与权利范围记录、确认是否沿用固定候选；不需要假填后端运行阶段。现有视频/PNG 的历史本机核验继续有效归档，本次缺的是获授权执行位置和用途/接收范围，并非声称没有原视频。

## 固定身份与阶段记录

检查器用代码内固定 SHA-256 读取 manifest，再按其 hash 读取原执行合同。model revision、两件模型文件描述、llama.cpp commit、build options、server arguments 都必须精确匹配；不容许浮动 main、不同量化、改变 context/host/sampler。packet 里的 manifest/hash 也必须匹配。

build、runtime、gpu_no_generation、synthetic_schema_generation 四阶段均要求 completed、整数 exit_code=0、非空私有证据引用、证据 SHA-256、同一 binary SHA-256、同一 provider/host。这只检查声明关联，证据实际字节及时间线仍需独立复核。失败与 delivery unknown 应保留原回执，不把状态改成 completed。

未来独立审阅须在获授权主机核查：

1. **build**：固定 source commit、构建参数、工具链、完整 stdout/stderr、退出码、binary 实际大小/hash。资格包的 binary 字串相同不证明已构建。
2. **runtime**：运行已有 record-runtime 后，保留实际版本、binary、CUDA/driver/显存/RAM/磁盘/依赖及 host 绑定；用现有 validate_runtime_record 验锁。单有 --help、截图或 provider 型号不够。
3. **gpu_no_generation**：只在完整权利/接收/资源授权和 CPU gate 通过后，按旧 gpu-preflight 流程检查 /props、F/HF token 上界、停止回执。它会加载真实权重和接触既有私有请求，**本次禁止执行**。无生成通过不证明 JSON Schema generation 支持。
4. **synthetic_schema_generation**：未来另获真实模型执行许可后，在同一主机/binary/model/参数下，单独目录、两个独立 text-only 请求，各对应固定 F/HF schema 一次，顺序 F/HF，retries=0，最多 2 次 generation、每次最多 2048 tokens/60 秒，完整保存 request/raw response/usage/finish_reason/schema 验证/停止记录。输入仅预先固定的 synthetic 文本，不能含媒体、base64、历史真实模型输出或人工 GT；两臂应标明 synthetic，不能伪称看过 k01–k04。不得占用/改写 Task6 正式 one-shot 账本。缺失、异常、截断、无法判定送达则停止，不重试或修补输出。

第 4 项的预算是未来资格探针建议，当前尚无获授权主机、模型执行授权或真实运行；本次工具自身 generation_calls=0。记录中的 2 次是该未来探针协议必须声明的实际完成数，并不表示检查器派发了请求。

text-only schema probe 最多提供固定配置能产生完整合规 JSON 的必要证据，不能证明约束解码机制严格执行所有 schema 关键字、multimodal 路径兼容性或视觉正确性。Schema 是否被后端忽略也可能碰巧得到合规输出；应保留 request 中实际 response_format/strict、后端日志与失败信息供人工审阅，不能仅凭合规输出宣布正式资格合格。

## 无模型 schema 检查

```sh
python experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/tools/task6_qualification.py schema-check \
  --arm F --content /path/to/authorized/synthetic-content.json
```

仅对命名 JSON 做本地 Draft 2020-12 校验，使用原 execution-input-contract.json 中的 content_schemas，不把 offline_runner 的 validation-v2 profile 替换进冻结 runner。exit 0 只表示该 JSON 满足这一 schema，仍不授予执行权限。工具不解包模型 response envelope、不验证其来源，未来 raw response 的提取/finish_reason/账本检查仍用既有审计工具。

Synthetic 单元测试包含：F/HF valid unknown、past 不能作为 current evidence、F 禁历史、重复座位、额外字段、错误 arm/null、SOP/机械锁/力状态从 unknown 越权写为完成，以及 provider/host/binary 漂移、失败阶段、0/超限生成数、私有媒体探针冒充 synthetic、未停止及未校验完整响应。它们是开发软件反例，不是模型输出、人工真值、真实负样本、准确率或正式研究效果。
