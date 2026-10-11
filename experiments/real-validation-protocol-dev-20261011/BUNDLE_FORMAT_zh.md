# 离线真实评估输入包格式

版本：`steptrace-real-validation-input/1.1`；冻结 SOP artifact 使用 `steptrace-sop-evidence-rules/1.1`，评分结果使用 `steptrace-real-validation-score/1.1`。这份格式只供未来授权真实材料接入使用。测试中的文件都是临时合成字节，不是样本。当前没有真实 bundle 可运行。

## 文件布局与来源

`--bundle` 指向一个 UTF-8 JSON 对象，`--root` 是其全部相对文件路径的根。路径必须位于根目录内，不接受绝对路径或越界路径。所有媒体、帧、裁切、规则和回答文件都必须存在且 SHA-256 相符。

顶层必需字段：

- `schema_version`: `steptrace-real-validation-input/1.1`
- `scope`: `DEVELOPMENT_ONLY_REAL_VALIDATION`
- `evaluation_id`
- `sop_artifact`: 冻结 SOP 证据规则 `{path, sha256}`。其 SHA 必须等于 `shared_run_contract.sop_sha256`。
- `shared_run_contract`: `protocol_sha256`、`sop_sha256`、`prompt_sha256`、`output_schema_sha256`、`model_fingerprint_sha256`、`decode_config_sha256`。每个方法臂必须逐字段一致。
- `source_events`、`opportunities`、`arms`

每个 `source_events` 项包含 `source_group_id`、`source_event_id`、`split`、来源说明与授权/许可依据、视频 `{path, sha256}`、帧清单 `{path, sha256}`。一个 `source_group_id` 只能属于一个 `development`、`calibration` 或 `validation` 划分；正式描述性计分只接受 validation 机会。

帧清单为 JSONL，每行包含 `frame_id`、整数 `pts`、有理数文本 `time_base`、原帧文件 `path` 与 `sha256`、`source_video_sha256`、正整数 `width`/`height`。每行的来源视频哈希必须与实测视频哈希相同。清单中的帧文件也逐一实测哈希。这个校验绑定记录和文件身份，不替代独立的来源/许可审查。

## 机会、截止帧和盲标

每个 `opportunities` 项需有唯一 `opportunity_id`、来源组/事件、`target`（`host`/`part`/`interface`）、`sop_step_id`、`challenge_type`、`cutoff`（`pts` 与 `time_base`）、`prefix_frames` 和 `blind_review`。

`prefix_frames` 必须与帧清单中从事件起始到 cutoff 的全部帧完全一致，包括顺序、PTS、time base、SHA 和尺寸。不能只挑答得好的帧；任何晚于 cutoff 的帧都会阻断计分。

`blind_review` 必须含两个独立标注流，每流至少两位不同标注者，且每行 `blind_to_methods: true`：

- `prefix_only`：只能引用 cutoff 前缀帧；分别标 `current_relation`（connected/disconnected/unknown）、`identity_status`（same/different/uncertain）、`interface_visibility`（visible/occluded/partly_occluded/unknown）与证据帧。
- `full_video`：可引用完整事件帧；标 `step_reference`（complete/incomplete/unknown）、挑战类别、`event_truth`（relation_held/relation_separated/identity_changed/unknown）、是否观察到过程和证据帧。`occlusion_hold` 必须由全事件证据支持关系跨遮挡间隔保持；“遮挡中没看见分离”本身不够。
- `adjudication`：必须是第三位独立且对方法输出盲态的裁决者（`blind_to_methods: true`），`status: resolved`，提供上述字段的裁决标签。裁决字段需分别列出 `current_state_support_frame_ids`、`step_reference_support_frame_ids`、`event_truth_support_frame_ids`。当前关系、身份和可见性只能由截止前缀帧支持；步骤及完整事件真值可由事件全帧支持。旧的宽泛 `support_frame_ids` 不能作为当前状态证据。unknown 是允许且需要显式写出的真值；缺失标签、待裁决或越界证据会阻断计分。

冻结 SOP 规则文件的每条 rule 必须有非空 `criterion` 和 `allowed_evidence_bases`。对 `full_video` 的 complete/incomplete 步骤标签以及裁决标签，`step_reference_rule_id` 必须指向同一 `sop_step_id` 下的一条冻结规则；unknown 标签可以将该字段设为 null。缺少规则或规则关联的 complete/incomplete 参考标签无法进入计分分母，整包在计分前阻断。这样可区分“标签未知”和“标签没有可核验的冻结步骤标准”。这些必填关联使此接入格式升级为 1.1；旧版 1.0 输入不按新格式解读。

validation 划分必须有经裁决且有来源证据的三类机会：`occlusion_hold`（完整事件证据表明关系保持，但 cutoff 的当前直接关系为 unknown、同一对象、接口遮挡）、`true_removal`（同一对象且完整事件证据支持直接分离）、`identity_change`（完整事件证据支持目标身份变化）。这只是覆盖类别的接入门槛，不是样本量、功效或充足性结论。若事件无法由来源视频/独立参考核实，应将事件真值保留为 unknown，不能用合成例补足。

## 三路输出

`arms` 必须恰好包含以下三路，每条机会各有一条输出：

| `method_id` | `view_mode` | `update_mode` | `reset_policy` |
| --- | --- | --- | --- |
| `full_frame_batch` | `full_frame` | `batch` | `per_opportunity` |
| `local_crop_batch` | `local_crop` | `batch` | `per_opportunity` |
| `temporal_memory` | `full_frame` | `incremental` | `per_source_event` |

所有输出都需记录同一 cutoff、相同 `input_frames`（帧 ID/PTS/time base/SHA/尺寸）、渲染后 `presented_frames`、原始响应文件及 SHA、`response_parse_status`。每路帧集合必须与机会前缀逐项相等。

`response_parse_status: parsed` 时，响应文件须为 UTF-8 JSON，字段恰为本格式中的 13 个规范化输出字段；文件内容必须逐字段等于同一输出行的计分字段。解析状态或原始响应与计分行不一致会阻断计分。格式无效输出可标为 `malformed`，但一个完整的 13 字段规范化 JSON 对象不能标为 malformed；对应行还必须将关系和步骤判定归一为 unknown/insufficient、清空历史与证据引用。malformed 按拒判计入 coverage，而不是缺失机会。两种状态下仍校验原始响应文件 SHA。

local-crop 还需 JSON `crop_rule_artifact` 与 `crop_rule_sha256`、`crop_rule_frozen_before_outputs: true`。规则需冻结坐标系、截止前缀来源、源帧绑定、RGB、PNG 输出、Pillow 名称与精确版本、nearest/bilinear/bicubic/lanczos 重采样方式。每个裁剪行需提供 `crop_source_frame_id`、`crop_source_sha256`、`crop_box_xywh`、`output_size` 和规则 SHA。计分器会打开哈希核验后的帧资产与裁剪资产，按规则重新裁剪/缩放并逐像素比较；仅有路径和哈希不够。运行环境缺少 Pillow、版本与冻结规则不一致或无法解码任一图像时，local-crop 臂和整包计分都会阻断。此像素比较证明裁剪与帧清单所指的帧文件一致；它不单独证明该帧文件确实由来源视频解码而来，来源视频/帧清单归属仍需独立核验。

时序臂另需按来源事件提供唯一 `temporal_runs`：每事件只在首帧前 `reset_before_first_frame: true` 一次，帧摄入序列不得超过该事件最晚 cutoff 的前缀；`emissions` 按 cutoff 排序列出每个机会，并逐个绑定其已处理的完整前缀。每个输出的 `temporal_session_id` 必须绑定到对应事件会话。

每条输出把以下内容分字段保存：

- `direct_relation`、`current_estimate`、`basis`、`assumptions`
- `historical_record_ids`、`history_applicability`
- `process_evidence_frame_ids`
- `step_completion`、`step_evidence_basis`、`step_evidence_rule_id`、`step_evidence_frame_ids`

若 `direct_relation: unknown` 而 `current_estimate: connected`，必须使用 `basis: conditional_history`，标 `history_applicability: conditional`，提供历史记录 ID 和非空假设。unknown 不可转成 disconnected。若当前直接关系明确 disconnected，任何旧历史都须保留为记录并标 `revoked`，不得继续作为当前 connected。

步骤完成与关系分开：SOP 文件内必须包含每个机会所引用的 `sop_step_id`，每条被输出引用的 rule ID 必须存在于该步骤下。`process_observed` 和 `sop_rule_supported` 的 complete/incomplete 声明都需引用规则和截止前缀证据；过程证据还需有对应过程帧引用。模型使用了规则未允许的证据依据时，不阻断计分；该确定性输出仍按 complete/incomplete 评分，并在 `rule_basis_violation_n` 中单独计数。若输出 `step_completion: complete` 且 `step_evidence_basis: conditional_history`，验证器会保留它并按完成声明计分；真值为 incomplete 时计入 FCR 分子。这是模型错误，不是输入缺陷。unknown 必须标 `insufficient_evidence`。条件历史本身不能变成步骤证据。

## 计分与停止条件

工具只有在媒体/哈希、每类盲标参考、来源分组、cutoff 前缀和三臂全部齐备时才计分；否则返回 `BLOCKED`、退出码 2，不写分数文件。三臂输出含 unknown 是已存在的拒判，可纳入 coverage；缺少某机会输出不是拒判，整包计分阻断。

- `FCR`：真值 incomplete 中输出 step complete 的比例。若 incomplete 分母为 0，结果为 null。
- `coverage`：真值 complete/incomplete 中输出 step complete/incomplete 的比例。
- `abstention_rate = 1 - coverage`。真值 unknown 单独计数，不进 FCR 或 coverage 分母。
- `rule_basis_violation_n`：确定性模型输出所引用的冻结规则不允许其 `step_evidence_basis` 的机会数；仅作输出错误诊断，不改变 FCR 或 coverage。

结果按全体机会及三种挑战类别分别列计数与比例，状态固定为 `DEVELOPMENT_DESCRIPTIVE_ONLY`。不计算显著性、功效或覆盖率匹配，不产生确认性比较、准确率或普适增益结论；这些设计项仍按协议待真实样本清点后冻结。
