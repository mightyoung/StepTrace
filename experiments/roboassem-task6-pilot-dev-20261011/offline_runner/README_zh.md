# RoboAssem task 6 离线合同与预检

**状态：Mac 输入合同已录入本地清单；未上传 Kaggle、未下载权重、未推理。执行仍被独立门控阻止。**

此包只检查提示/参数/帧映射、实际本地字节和真实响应 JSON。它不实现模型客户端或启动服务。Mac 报告的系统提示与共同问题 SHA256 已在本机对照复算；四张遮罩 PNG 的文件名、字节数和 SHA256 已录入，但云端没有 PNG 字节，尚未独立重哈希。清单不含图像、base64、端点、凭证或绝对私有路径。

## F/HF 输入合同

- **F** 只有 k04；**HF** 顺序为 k01、k02、k03、k04。F 与 HF 的 k04 描述完全相同，都是 223,238 字节、SHA256 `7fa67434a107f5086628a5175c74a9a7935ff2992b0ef10a4d1ffed0506645a8`。
- 四张模型输入均为 640×480 PNG，来自 Mac 报告的遮罩派生图。遮罩为黑色不透明矩形 `(x=0,y=400,w=112,h=80)`；未遮罩原帧仅保留来源字节元数据，标记为不可作为模型输入。
- 系统提示和共同问题的 UTF-8 SHA256 分别为 `4f08f7f8529e2badafa35cc80a66bf781b8f6bff376102556f1e762f0e273a57` 与 `160e6d72526c226aa4f124abf7bf675840b2b74be467ddfb483a6ca8495f48d6`。条件角色文字及每张图片前的文字块按 Mac 合同分别记录；不重写或借用其他实验提示。
- 完整多模态请求体哈希仍未知：后端序列化方式及云端 PNG 字节缺失。清单不会把文本片段哈希冒充完整请求哈希。

## 参数与执行门控

录入的 Mac 参数是 `Qwen3.5-9B-Q4_K_M`、temperature 0、seed 1、max_tokens 2048、stream false、`enable_thinking=false`；每请求超时 60 秒，F 后 HF，串行、零重试。`top_p/top_k`、后端名称/版本仍未知，未设置默认值。Mac 报告旧 Qwen 服务不可用；不再寻找该服务。

Kaggle 上传、模型下载和推理各自有单独授权状态，当前都为 `not_authorized`。另有既有模型/费用授权和计算授权检查。通过存在一个授权对象不能放行其他动作。

本机 `jsonschema` 的 `Draft202012Validator` 只用于离线验证，不联网或安装包。推理后端是否支持 strict JSON Schema 的 `contains/minContains/maxContains` 尚未核实；因此后端 schema 能力仍为阻断项，即使 CPU 合成测试通过也不会放行。

## F 与 HF 内容 schema

`schemas/F_visual_state.validation-v2.schema.json` 与 `schemas/HF_visual_state.validation-v2.schema.json` 分开验证。Mac 给出的原 schema 文件 SHA256 分别记录为：

- F：`77240e2c680ae3d5b4ec6f28083c32bdb54eb8e757b47e0678b63ac40c751b4d`
- HF：`45075bdcf0eb9144ddca918c74600901f4660f4b4a2d06b0601277b5b2193753`

这些是 Mac 原文件的**字节身份**。原 schema 文件字节未提供；本包不把重排后的 JSON 声称为相同文件。两个本地文件是基于提供的 schema 语义生成的验证配置，清单另记其本地 SHA256。离线验证配置 v2 增加了当前关系中左右座必须各恰好出现一次，保留输出 `schema_version: "1"`。F 仍强制 `identity_continuity: "not_observed"` 且历史数组为空；HF 历史证据只允许引用 k01–k03，当前关系只允许引用 k04。

请求端最终选用源 schema 还是离线 v2 配置尚待确认。即使真实响应通过本地 schema，后端 schema 支持未核实仍保持阻断。

响应检查先验证每次尝试、finish reason、上下文未截断、响应原始 JSON 字节大小及 SHA256，再按 request_id 用 F 或 HF 的独立 schema 离线验证 JSON 内容。校验器不合成、不补写、不重试模型响应。测试代码里的显式合成对象仅用于软件测试，不是模型输出或研究结果。

## 运行 CPU 结构测试

当前执行环境已有 `jsonschema`，不安装依赖。运行：

```bash
python -m unittest discover -s experiments/roboassem-task6-pilot-dev-20261011/offline_runner/tests -v
```

对私有 PNG 输入执行预检：

```bash
python experiments/roboassem-task6-pilot-dev-20261011/offline_runner/offline_preflight.py \
  --manifest experiments/roboassem-task6-pilot-dev-20261011/offline_runner/request_plan.json \
  --private-input-root experiments/roboassem-task6-pilot-dev-20261011/offline_runner/private_inputs
```

真实响应文件应保存在私有目录。只有获得授权并已存在真实响应后，才可把记录路径交给 `--response-records` 和 `--private-response-root` 做本地 envelope、原始字节 hash 与 F/HF JSON Schema 检查；此命令不会调用模型。

当前清单会报告缺少私有 PNG、`top_p/top_k`、后端版本及 schema 支持、request-time schema 选择、三个独立动作授权、费用/计算授权等阻断，并以退出码 `2` 结束。这是预期的未解锁状态。

## Kaggle 草稿与仍未执行的工作

`kaggle_private_draft.ipynb` 只执行离线清单/文件/响应校验，没有上传。Kaggle 上传、模型下载、两次 GPU 推理均为 0；模型文件也未下载。T4 对 BF16 projector 的兼容性未确认，不能静默换精度。

本分支没有模型答案、准确率、成功率或方法增益结论。Mac 提供的是输入合同，不是调用授权；授权三项状态分别需要明确批准。

解锁前仍需把四张遮罩 PNG 放入私有目录并实算核对字节数/SHA256；取得 `top_p/top_k`、后端名称/版本及 strict schema 关键词支持的正式记录；确认 request-time 使用的 schema profile；分别授权 Kaggle 上传、权重下载和推理，并确认费用与计算授权。T4 对 BF16 projector 的兼容性也未确认。
