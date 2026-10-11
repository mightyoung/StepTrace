# 过程证据与当前可见状态：离线对照

本实验只读取仓库内已核验的公开缓存答复 `q31/q68/q92`，并复用其声明适用性审计。没有读取图片或视频，没有新增模型调用、训练、媒体下载、依赖安装或生产链路接入。

## 结果

三份缓存来自同一真实事件的三个相关输入前缀，共一源事件、三个不同帧、六条重复观测行；它们不是三个独立物理样本，也不是人工真值。

| 缓存 | 过程输入 | 截止帧记录 | 状态依据 | 可保留的结论 |
|---|---|---|---|---|
| q31 / f1743 | `missedprocess`：采样输入未提供安装过程 | `external_connection_supported` | `direct_current` | 缓存答复直接支持该帧的外部连接结果；过程步骤完成仍为 unknown |
| q68 / f1746 | `missedprocess` | `occluded_or_unknown` | `conditional_history` | 保留 f1743 的历史连接；当前接口直接状态 unknown，条件估计仍依赖原答复中的两项假设；过程步骤完成仍为 unknown |
| q92 / f1752 | `missedprocess` | `external_connection_supported` | `direct_current` | 缓存答复直接支持截止帧外部连接结果；q92 中间 f1746 仍记录为 unknown；过程步骤完成仍为 unknown |

协议将 `missedprocess` 定义为输入未包含过程证据，不代表模型漏检。端点连接结果不能替代安装过程或 SOP 步骤证据；记录中的 `external_connection_supported` 也不构成人工核验的物理真值。

有损投影（估计关系标签 + 是否存在 active 历史）使 q31 与 q68 碰撞；加入最后直接观察、依据类型、条件和历史后，两者可区分为“当前帧记录直接支持”和“当前 unknown、条件历史保留”。两者的过程步骤完成均为 unknown。这是缓存字段表达能力的对照，不是方法准确率、生产缺陷或机制增益证据。

另有三个仅逻辑用途的合成夹具：遮挡后保留条件历史、明确分离后保留历史并撤销当前适用性、对象身份变化后保留旧历史但不转移到新对象。它们不使用媒体，不计真实负例或样本。

## 复现

在此目录使用 Python 标准库运行：

```sh
python3 -B -m unittest -v test_process_visible_state.py
python3 -B process_visible_state.py --output results.json
```

程序复用 `claim-applicability-dev-20261011/audit.py` 的 23 项公开文件 SHA-256 与 Git blob 校验，并固定缓存 manifest SHA-256。产物、来源提交、测试结果与哈希见 `verification.json` 和 `MANIFEST.json`。

## 未支持项

- 旧 IndEgo 119–128 秒真实拆卸的原帧清单及 H/F/HF 首答 JSON 仍未恢复；本实验没有据摘要重建真实分离对照。
- 缓存答复由模型生成，不是人工真值；没有独立像素或物理状态复核。
- 没有步骤映射或过程真值，因此过程步骤完成、识别准确率、过程准确率、误撤销率和机制增益均未测，保持 null/unknown。
- 输出只存在于本云端仓库工作树。`MANIFEST.json` 可用于 Mac 回存核验，但不能表示云端路径已在 Mac 上保存。
