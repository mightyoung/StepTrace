# 从连接原答到真实消费入口：边界验证

本轮接续父提交 `0a6aa25529ec169156b9ad979d03a13754e0a874`。原视觉缓存及生产代码固定于 `955d2bae7f047c5e699caa90ac094b74b9b738e3`，无新增模型调用、训练、视频下载或物理真值。

## 实际消费链

检查完整公开源码及配置/测试共306份 py/json/yaml 文件；connection_probe、steptrace-connection-probe、active_connection_ids、historical_connections、conditional_history 命中均0。current_estimate 名称命中11处，属于 StateMemory 的独立 stage-inference 容器及其测试，不是连接探针记录。token扫描只是线索，另逐段阅读以下真实入口。源文件和定位见 source_scan.json。

| 真实路径 | 接受/派生的信息 | 与三份 connection_probe 原答的连接 |
|---|---|---|
| StateRiskEngine.infer → validate_inference → StateMemory.append_estimate | 独立 schema 1.0 的 estimated_stage、completion_state、evidence_refs、state_estimation_only | 未找到转换；原答的 schema/字段不兼容 |
| run_vlm_state_risk → estimates.jsonl → predictions_from_run_dir | 验证后的 stage/completion，映射到独立评分机会 | 没有 basis/active 到 stage 的映射；缺 stage/completion 时输出 unknown |
| g1_derivation.derive_inputs | 严格 runtime trace + 独立 action CSV + 冻结 action-state map；离线产生校准/HMM标签 | 不读取 connection_probe 历史；原JSON不符合 trace schema |
| yolo_training_views.build_training_view | 已有图片配对 YOLO 标签文件，重映射类别 | 未找到连接原答到目标框标签转换；未执行图片派生或训练 |
| SOPStateMachine.observe | 私有签发的 DeterministicEvidence | VLM字段只供审计，单独不足以推进；未找到原答桥接 |
| VlmAuditOnlyAdapter 与 paired freeze | 前者不授权状态写入；后者要求独立 scorer-enriched 真值元数据 | 没有从本三包自动生成授权或独立真值的转换 |

结论范围：在本固定公开源码内未发现三包原回答/实验账本到 stage scorer、训练标签或完成判定的实际转换。不能推广为外部/私有 glue 不存在，也不称修复已有生产漏洞。stage scorer 本身不是通用 provenance 认证器，假造合法 stage 字段不能由本扫描排除；正常引擎链先验契约，未实现连接证据到步骤完成的授权映射。

## 直接运行真实入口

consumer_boundary.exercise_real_consumers 对 q31/q68/q92 的原JSON逐份运行真实函数，结果为：

- validate_inference：3份均 schema不兼容拒绝，拒绝发生在知识/窗口内容处理前
- StateMemory.append_estimate：3份均拒绝，无 current_estimate 或 estimate_history 写入
- predictions_from_run_dir：3份均 unknown，没有导出 estimated_stage/completion_state
- G1 derive_inputs：3份均 trace line 1 schema mismatch，无训练标签产物

评分适配器与G1入口前置条件使用明确命名的 synthetic 软件路由夹具，只为到达真实解析分支。它们不是视频步骤标注、不是给 q31/q68/q92 补GT；本轮不调用 score_state_risk、不计算分数、不拟合模型。直接函数验证和静态源码阅读不能代替端到端视频执行。

## 隔离适配和拒判

新适配器只为固定三记录缓存产生 evidence envelope，保留当前直接观察、basis、全部 assumptions、历史与未观测间隔。q68 current observation 仍 unknown，conditional estimate 仍有条件 connected。每次消费将整个 envelope 重新绑定到哈希已核的原缓存，不能信任调用者可改的 eligibility 布尔位。

cache_record_audit 可用；current_visible_record_support 只允许 q31/q92 的AI记录端点资格，q68拒绝。全部三包的 stage_scorer/completion_decision/training_ground_truth 请求均拒绝：没有获授权的步骤映射或独立真值。该接口不接入生产链，也不以原模型标签冒充监督真值。

7项新增软件测试通过，包括真实消费者边界、unknown/条件传输、历史与原缓存绑定、deep copy、未授权stage/完成/GT请求，以及5类合成传输改写（删条件、改basis、改当前unknown、伪造资格、插入stage映射）。connected/active有损投影仍是预期 RED 碰撞负对照，完整证据规则同样可由普通FSM实现，不能证明算法增益。

## 测试、失败与未测

```sh
# 本目录：仅标准库实验代码 + 仓库已有模块；不安装依赖
python3 -B -m unittest -v test_consumer_boundary.py
python3 -B consumer_boundary.py --output results.json
python3 -B scan_consumers.py --output source_scan.json

# 仓库根目录：当前环境已有 pytest
python3 -B -m pytest -q tests/test_vlm_state_risk_contract.py tests/test_vlm_state_memory.py tests/test_state_machine.py tests/test_state_risk_metrics.py tests/test_g1_derivation.py
```

新增7项通过；较宽既有检查61通过、1失败，不能称全套通过。失败是 test_shipped_knowledge_packs_are_explicitly_non_formal_and_valid 只允许 meccano-development.yaml/software-fixture.yaml，但固定源码还含 gv06-reference-port.yaml。文件同基线核验一致，本轮未修改其列表或知识包，保留既有不一致，不删除第三包。

第一次运行新路由夹具时 enabled_risk_ids 为空，被评分器在前置校验拒绝；修正为 synthetic-risk 后到达目标分支。失败记录保留在 verification.json，不能把前置拒绝当作连接消费结果。

未测：私有/外部转换、真实模型/视频端到端路径、实际训练运行、隐藏期间保持/身份/漏步物理真值。旧真实分离原答和输入仍缺。物理误撤销率 null，算法增益 null。下一步先恢复有权使用的真实分离原记录与独立真值；若要连到生产 stage，需要冻结步骤映射与过程证据要求，再做同信息强基线比较。当前端点外部连接不等价于步骤完成。
