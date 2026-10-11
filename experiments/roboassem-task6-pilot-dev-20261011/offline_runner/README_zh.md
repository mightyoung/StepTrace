# RoboAssem task 6 离线预检包

**状态：仅离线结构草稿；未上传 Kaggle、未下载权重、未调用模型。**

本目录只固定 F/HF 请求描述与做结构检查。它不实现推理客户端，也不启动本地服务。当前 `request_plan.json` 明确保持 `execution_ready: false`。帧路径只是相对占位符，没有视频、图像、私有路径或 Library 标识。

## 已固定的候选模型元数据

官方 Hugging Face 仓库的候选修订为 `3885219b6810b007914f3a7950a8d1b469d598a5`。该修订下页面列出的文件为：

| 文件 | 页面显示大小 | SHA256 |
|---|---:|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | 5.68 GB | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| `mmproj-BF16.gguf` | 922 MB | `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383` |

页面也列出对应 Xet hash，见清单。页面大小经过四舍五入，精确字节数未在此记录。元数据来自 [固定修订下的主模型文件页](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf) 和 [BF16 projector 文件页](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/3885219b6810b007914f3a7950a8d1b469d598a5/mmproj-BF16.gguf)。只读了公开页面元数据，没有下载权重。

这是待比较的候选版本，不代表历史 Qwen 服务的精确复现。历史服务当前是否可用、实际运行时、参数和费用授权仍未核实。T4 对 BF16 projector 的兼容性也未验证；包中禁止静默换成其他精度。

## 请求与响应完整性

- F 只列当前帧 PTS 510；HF 按 PTS `[0, 300, 450, 510]` 排序，并与 F 指向同一当前帧描述。PTS、time base `1/30` 和视频时间来自 Mac 执行者报告，本云端没有检查像素或文件字节。
- 计划上限为两个独立请求、`max_retries=0`、`parallelism=1`，未来服务仅允许绑定 `127.0.0.1`。这里没有服务代码或网络调用。
- 结构校验检查请求 ID、帧顺序、共享当前帧、相对路径、文件大小/SHA256、响应请求 ID、单次尝试、`finish_reason`、上下文截断标记和响应字节 SHA256。
- `response_envelope.schema.json` 只描述通用完整性字段。task6 的固定回答提示和语义 schema 缺失，因此不会判断答案内容，也不会生成假模型输出。
- 若之后已有真实响应记录，可把响应原始字节保存在私有目录，并用 `--response-records` 与 `--private-response-root` 检查文件大小和 SHA256 是否匹配。校验器不生成或补齐响应。

## 离线检查

仅用标准库运行 CPU 结构测试：

```bash
python -m unittest discover -s experiments/roboassem-task6-pilot-dev-20261011/offline_runner/tests -v
```

运行清单预检：

```bash
python experiments/roboassem-task6-pilot-dev-20261011/offline_runner/offline_preflight.py \
  --manifest experiments/roboassem-task6-pilot-dev-20261011/offline_runner/request_plan.json \
  --private-input-root experiments/roboassem-task6-pilot-dev-20261011/offline_runner/private_inputs
```

对之后真实保存的响应记录，可另加 `--response-records <私有记录.json> --private-response-root <私有响应目录>`。

在当前草稿上，预检会列出缺少私有帧、提示、运行时参数和授权等阻断项，并以退出码 `2` 结束。这是预期结果，不是运行失败的模型实验。

## 私有 Kaggle Notebook 草稿

`kaggle_private_draft.ipynb` 只包含离线文件检查单元，路径需在私有副本中按实际输入调整。当前 Notebook 没有上传。尚未确认 Kaggle 上传、模型下载或两次免费 GPU 推理；这些步骤均未执行。

## 解锁前缺少的最小材料

1. task6 原执行者的固定提示文本及其 SHA256；不能挪用其他实验提示。
2. PTS 0/300/450/510 四张私有输入图的相对文件、字节数和逐帧 SHA256；Mac 报告中的 27 个文件总体验 hash 结果没有附逐文件摘要。
3. 既有后端和版本、完整推理参数、响应内容 schema，以及既有模型/配置/费用和计算授权记录。
4. 若选择 T4，先确认 BF16 projector 可用性；兼容性未知时暂停，不能改精度后假称同配置。

收到材料也不意味着自动发起调用。此分支当前没有模型响应、准确率、成功率或方法增益结果。
