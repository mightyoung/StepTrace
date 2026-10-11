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

## 固定版本独立复验闭环与真实材料缺口

针对固定代码提交 `841f571b169182ca1791b582cb6320f32cb93105`，由 root 独立复验者核查：30/30 focused tests 通过，分类标记 `C0/I0`，原 ST3、ST4 可关闭。本段仅追加复验状态，不覆盖上文旧复审结果或其他研究结果。

- ST3 在合法 input/SOP 1.1 下以空 `rules: []`、重算并同步 artifact SHA 的变体确实因真值关联缺失而 `BLOCKED`，不是被旧版本号拒绝。分别删除 adjudication 关联、删除 `full_video` 关联、将 criterion 改为空白，也都明确阻断。
- ST4 保留合法 rule ID、`conditional_history` 与 `complete`，仍 `SCORED`：合成夹具 FCR=`1.0`、coverage=`0.5`、`rule_basis_violation_n=1`。
- 仍然不存在的 rule ID 是接入完整性错误，继续阻断；本结论没有声称所有无效 rule 输出都应计分。
- 冻结 `PROTOCOL_zh.md` 与基线字节一致，SHA-256=`0a162602082796627838dc9e6463300196b72925db09311dd6201355bd573562`。
- 本次只有已有软件测试复验，没有真实视频实验、人工真值复核、模型调用或训练；这些 ST3/ST4 变体仍是合成契约夹具。

### 进入真实实验还缺什么

当前最小归档恢复请求仍是一个可访问的本机路径或准确 Library 文件名，指向旧 IndEgo 真实拆卸包：119–128 秒、相机支架底座与长立柱分离；包内需有原帧 manifest 和同一输入对应的 H/F/HF 原始首答 JSON，最好同时含提示词、帧映射及原媒体身份。现有摘要或单张图不能替代这些原始记录。

已登记的公开来源身份可用于核对，但不证明素材已在云端或 Mac 可读：source ID `INDEGO_232cf338`；文件 `1_Disassembly/mp4_480/232cf338-98a7-4cdc-8683-417c0e2adf7a_480.mp4`；登记大小 13,326,100 bytes；登记 SHA-256 `3552fbc900518a3499191be00e9897957dee562d9bdc59a23ec0c8b1d499c490`；10 fps，time base `1/10240`。历史候选记录把 124.8 秒列作保守分离点，但它是 AI 初筛，不是人工真值或精确脱开边界。来源 URL 与许可元数据保存在 [证据索引](../../docs/research-handoff/progress/evidence-20261010.json)。

恢复时可先对已有本地媒体核对身份，不发起下载：

```sh
shasum -a 256 "$INDEGO_MP4"
stat -f '%z %N' "$INDEGO_MP4"
```

预期大小和 SHA-256 见上。已有公开缓存进度对照的复现命令在 [恢复记录](../recovered-real-cache-dev-20261011/README_zh.md)；它只核验保存的缓存日志与渲染视频，不会恢复缺失的 IndEgo 原帧或 H/F/HF JSON。完整真实 bundle 1.1 到齐后，现有离线入口为：

```sh
python3 -B experiments/real-validation-protocol-dev-20261011/validate_bundle.py \
  --bundle "$BUNDLE_1_1" \
  --root "$ASSET_ROOT" \
  --output "$SCORE_PATH"
```

此命令尚未运行：真实 bundle 不存在。要进入正式真实评估，除上述分离归档外，还需可核验的授权来源视频及逐帧 manifest、三类真实挑战（遮挡保持、真实分离、身份变化）的独立盲标/裁决与冻结 SOP criterion，以及相同 cutoff/运行契约下三路方法输出。已有 q31/q68/q92 仍是同一开发事件的相关缓存；G-V0-6 的 AI 审阅不构成人工 GT；MECCANO-0009 仍是 development 且带正式 test tombstone。它们都不能替代缺失材料。

在获得原包路径/文件名与合格来源、真值前，保持真实实验未运行；不再生成合成测试作为主线进展，也不从摘要补造原始记录。
