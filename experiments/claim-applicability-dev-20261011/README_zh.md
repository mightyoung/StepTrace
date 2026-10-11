# 当前证据与条件历史：缓存声明适用性验证

固定来源：mightyoung/StepTrace，完整提交 `955d2bae7f047c5e699caa90ac094b74b9b738e3`。本增量只核对公开 AI 记录、字段适用性与代码守卫，不运行视觉推断，也不接入生产步骤推进或评测器。

## 实测结果

| 缓存 | 末帧 AI 标签 | basis | 新适用性标签 | 当前可见确认资格 |
|---|---|---|---|---|
| q31 | external_connection_supported | direct_current | direct-supported-by-record | 有，仅限记录支持的外部端点 |
| q68 | occluded_or_unknown | conditional_history | conditional-history | 无；保留原历史与两条条件 |
| q92 | external_connection_supported | direct_current | direct-supported-by-record | 有，仅限记录支持的外部端点 |

q31/q68 的原 current_estimate 均为 external_connection_supported 且 active 非空。只投影 relation_state/active 会输出相同标签：`RED_expected_projection_collision`。这是刻意有损的负对照，不能用于当前可见确认监督。q68 原答案合规；有条件 connected 并非错误，也没有发现相应生产漏洞。保留 basis、last_observation、条件、历史的普通强基线同样可以区分，不构成账本胜过 FSM 的证据。

q68/q92 的 f1746 源帧 SHA256 一致，两份原观察均为 unknown；q92 在 f1752 再显后的支持没有回填 f1746。计数为一个源事件、三个独立上下文前缀、三张独特图、六个重复计入的观测行，绝非跨轮记忆试验。

10项软件回归检查通过。五个明确合成的坏记录全部 `PASS_rejected`：删条件并改称当前直接确认；将 q92 中间帧改为连接；将未观测间隔 changes_excluded 改 true；将模型复制记录伪称 human GT 或 independent GT。它们是代码负例，不增加真实视觉样本。额外检查条件缺失、未知身份弃答、无绑定 active ID 和未来截止时点。

## 实际消费路径和边界

完整固定提交的路径清单无 AGENTS.md、handoff.md、research-brief.md、.agents 或项目 skills。读取了已有研究交接、当前简报、研究方向和历史发现。已有 src/display_project/vlm_sop/state_risk_contract.py 的 validate_inference 对 completed 缺所需观察会拒绝；metrics/state_risk.py 的 predictions_from_run_dir 消费 estimated_stage/completion_state。源文件 blob 已同固定提交核验，src/scripts 搜索未找到 connection_probe/active_connection_ids 的生产消费桥接。因此适配器隔离于本实验目录，不为三包补 estimated_stage 或步骤完成真值。

`adapt` 依赖独立通过清单哈希检查的冻结事件参考，保留原历史、条件、未观测间隔与 AI 来源。其 provenance 字段检查只拒绝输入中的来源升级声明，不能鉴定现实来源；hash/标签一致也不能证明像素或物理真值。针对本缓存，变化排除与帧标签篡改都拒绝；不声称通用视觉审查器。原答案及既有清单未改写，派生结果单独存储。

物理保持负例数仍为0，物理误撤销率为 null/N/A。隐藏期间关系 unknown，机械牢固 unknown、安全未评估；没有识别准确率提升、训练依据或机制增益。本次没有重复 FSM/账本回放。

## 复现

在本目录运行（仅 Python 标准库，无安装、视频或模型调用）：

```sh
python3 -B -m unittest -v test_audit.py
python3 -B audit.py --output results.json
```

results.json 保存完整标签、条件、历史、逐帧哈希、负对照和五个合成拒绝原因。verification.json 记录环境、命令、退出码与产物哈希。原包 public_manifest 的23条字节数/SHA256/blob 全通过；原清单自身的 SHA256 单列在新结果中，清单中的 base_commit 是历史父提交，不冒充当前固定来源。

Mac 恢复从既有本地公开源码开始，补齐远端公开文本；普通 Git 远端访问的失效代理/DNS失败被保留为环境限制，随后使用已有授权 GitHub 连接器。未修改本机记忆、未安装依赖、未配置密钥、未下载媒体、模型首答新增0。

## 未恢复证据与下一步

旧真实分离例在历史报告有叙述，但其原输入和原首答不在本公开缓存内，未造对照。当前优先恢复其可授权的原始记录及哈希，再以同信息的完整 basis/观察/历史/条件规则检查分离与仅证据变化。独立的隐藏期保持真值、相似换物身份真值与实际漏步骤真值仍缺；获得前不计算物理误撤销率或识别效果。若原材料不可恢复，明确记缺失，继续停止新模型、训练与视频下载。
