# 离线验收补充复审：步骤真值与模型输出

复审基线：`525a76cf33ca8b1626949a2c2c719cbe870af89d`，分支 `task/current-visible-state-contrast-20261011`。本记录仅覆盖两条新发现；既有 [复审验收](REVIEW_ACCEPTANCE_zh.md) 与其逐版本记录保留不变。

## 复现和处理

| 发现 | 基线表现 | 修后要求 |
| --- | --- | --- |
| ST4：有效规则 ID 下，`step_completion=complete`、`step_evidence_basis=conditional_history` | 规则不允许该依据时，验证器先阻断，模型错误没有进入 FCR。 | 保留完成声明并计分；若真值 incomplete，则计入 FCR。输出所用依据不受规则允许时，另计 `rule_basis_violation_n`。 |
| ST3：步骤 SOP 的 `rules` 为空，方法臂均输出 unknown，但裁决仍给 complete/incomplete | 未关联冻结步骤规则的真值照常进入分母，合成夹具得到 FCR=0、coverage=0。 | 每条 complete/incomplete 的全视频标注和裁决必须经 `step_reference_rule_id` 关联到机会所指 SOP 步骤中的非空冻结 criterion。关联缺失或无效时在计分前阻断，不能把无标准真值计入分母。 |

SOP 每条规则现在需保存非空 `criterion` 和允许的证据依据。模型输出引用不存在的规则仍会阻断，因为那是无法解析的引用；引用有效规则但使用不允许的依据则是可评分的模型错误。unknown 参考标签可将规则 ID 设为 null。

两条基线复现的完整变更和观察值见 [基线记录](review-followup-baseline-525a76c.json)，修后记录见 [结果记录](review-followup-after.json)。测试夹具只验证输入参考资格与错误输出的计分路径，不能当真实遮挡、拆卸、移除或身份变化样本。

## 边界

- 不改变冻结协议 `PROTOCOL_zh.md`；新字段和校验要求属于开发接入格式及其离线验证器。
- 开发输入、SOP artifact 和 score schema 递增为 1.1，以标明新增必填真值关联及诊断计数；旧输入不被默认为兼容。
- 不使用模型、训练、下载、安装、付费 API 或凭证；没有运行真实 bundle。
- 公开缓存仍是同一开发事件的已存记录，不会因本次合同测试变成独立真值或新识别结果。
- 独立只读复审覆盖完整补充差异及 schema 版本更新，建议推送、无阻断项；复审者重跑 30 项验证测试并全部通过，未修改文件、未推送。结果见 [修后记录](review-followup-after.json) 和 `verification.json`。
- manifest 中的路径描述云端工作区文件；它不证明已写入 Mac。Mac 回存仍需按文件字节数和 SHA-256 核验。
