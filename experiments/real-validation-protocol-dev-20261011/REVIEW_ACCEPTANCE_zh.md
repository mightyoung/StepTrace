# StepTrace 离线验收复审记录

复审对象：`5fe0e4fc019bb58d72b95800e3b36fe6949209ae`（研究分支 `task/current-visible-state-contrast-20261011`）。

## 结论

独立只读复审建议批准推送。复审者核对了本记录所列五项基线问题及其修正，并发现了一个额外问题：完整规范化回答可被错误标成 `malformed` 后按拒判隐藏。现已增加验证器阻断和回归测试；复审者确认该修正关闭了复现路径，并在两组离线测试共 27 项通过后建议批准。

## 修复对照

| 检查项 | 基线 `7f919e5` | 当前结果 |
| --- | --- | --- |
| 未来帧像素伪装成 cutoff 裁剪 | 接受 | 阻断。逐像素重建并比较裁剪文件与哈希核验后的前缀帧文件。 |
| 原始回答与评分行矛盾 | 接受 | 阻断。解析规范化回答并逐字段比较。 |
| 未冻结的 SOP rule ID | 接受 | 阻断。校验实际 SOP 文件 SHA、步骤 ID、规则 ID 与允许的证据依据。 |
| `conditional_history` 下的 `complete` | 错误阻断 | 保留为完成声明；不完整真值时计入 false-complete/FCR。 |
| 仅由未来帧支持的当前关系裁决 | 接受 | 阻断。当前关系、身份和可见性只接受 cutoff 前缀证据。 |
| 完整规范化回答被标成 malformed | 可绕过原始回答核对 | 阻断。完整的 13 字段规范化 JSON 不能声明为 malformed。 |
| 全视频步骤/事件真值引用未来帧 | 应允许 | 仍允许；与当前状态证据字段分开记录。 |

详细的临时合成复现结果见 [基线记录](review-baseline-7f919e5.json) 和 [修后记录](review-reproduction-after.json)。所有反例只在单元测试临时目录中生成，不是视频、模型输出、研究样本或实验结果。

## 验证与边界

- `python -B -m unittest -v tests.test_steptrace_real_validation_bundle tests.test_steptrace_independent_review_regressions`：27 项通过。
- `python -B -m unittest -v test_process_visible_state.py`：6 项通过。
- `py_compile`、`git diff --check` 和缓存 HTML 回放生成均通过；HTML SHA 保持 `f693ba7a17e545cf9cf5e2ece4055befcb43656f74cfa284f93746dc5f4d0428`。
- 没有运行真实评分、模型调用、训练、媒体下载或依赖安装。真实材料、盲标真值和三路真实输出仍缺失，真实评估门保持关闭。
- 裁剪像素比较只证明裁剪与帧清单所指图像文件一致；它不证明该帧文件确实由声明的视频解码而来。来源视频和帧清单归属仍需独立核验。裁剪核验需要与冻结规则一致的 Pillow 版本；缺失或不一致时整包阻断。
- 冻结协议 `PROTOCOL_zh.md` 未改。当前云端工作区产物可按 manifest 核验；Mac 回存状态仍为 pending。

复审者说明：只有恰好具有完整 13 个规范化字段的 JSON 对象被禁止标成 malformed；带额外字段的对象不符合本格式，按协议作为格式无效拒判处理。该边界已向复审者说明并获其在当前格式契约下批准。
