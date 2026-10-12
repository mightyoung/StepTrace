# RoboAssem Task6 — 执行准入包（2026-10-12）

**状态：`preparation_only_blocked_for_execution`。** 本包供根审阅恢复顺序和运行门槛，不启动 Kaggle、不下载权重、不运行模型。候选是单条 Task6 开发小试，不是事件真值或完整 SOP 验证。

## 1. 固定候选身份

### 模型文件

| 文件 | 固定来源 | 预期字节数 | SHA-256 | 当前证据 |
|---|---|---:|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | `unsloth/Qwen3.5-9B-GGUF`, revision `3885219b6810b007914f3a7950a8d1b469d598a5` | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` | 既有 Mac/Kaggle 记录称下载并重哈希通过；模型字节不在云端 repo，本次不重下也未复验活动挂载 |
| `mmproj-BF16.gguf` | 同一 repo revision | 921,705,024 | `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383` | 同上；T4 BF16 projector 兼容性仍未实测 |

权重的精确大小与 SHA 取自现有 `sources/uploaded-setup.py` 和 Mac runner audit；较早 `offline_runner/request_plan.json` 中的大小为 null，是旧草稿记录，不覆盖这里。当前 Kaggle 工作区是否仍保留这些文件尚未核验；若只读校验时文件缺失或 hash 不符，必须停止，不运行下载型 setup cell，也不换精度/权重。

### 推理后端

- 源码候选：`llama.cpp` commit `8345f333951c661d166b00e6f9362e553768f292`。
- 构建候选：CUDA 开启、`CMAKE_CUDA_ARCHITECTURES=75`、Release、server 开启、tests/examples/curl 关闭；`llama-server` 最多 4 个 build worker，现有构建脚本单命令超时 480 秒。版本化源脚本 SHA-256 为 `120d880a870697678a59cf988d34547bcf3ce712baf1dde9787d9c1ec341f33c`。
- 预期 `llama-server` 参数：`--device CUDA0 -ngl 99 -c 16384 --parallel 1 --threads 4 --flash-attn off --image-min-tokens 1024 --image-max-tokens 2048 --no-context-shift --no-cache-prompt --host 127.0.0.1 --port 8080 --top-k 40 --top-p 0.95 --temp 0 --seed 1 --jinja --no-mmproj-offload`。
- **实际 backend 版本及二进制 SHA 尚未固定。** 上述是源码/构建候选，不等同运行时身份。准确缺少的运行产物是 Kaggle 工作目录中的 `runtime-verification.json`，字段应至少含 pinned commit、`llama-server --version`、binary SHA-256 和构建耗时。此前笔记只记录到 `Build 0`，没有成功 build 记录；本云端也没有该二进制。未得到此文件前，不进入推理阶段。
- 对 BF16 projector 的 T4 兼容性、backend 的 strict schema 支持及实效 context 均未确认；不得静默替换 projector、backend 或配置来“修通”。

## 2. 输入 manifest 与四帧预处理

视频身份来自 Mac 执行者记录：`task6_m_1.mp4`，3,708,210 bytes，SHA-256 `49e048efeb14f8da2c9d6a2a23707f721b76d46bf76a4280cb2a03edcbfc3a06`，H.264，640×480，30 fps，547 帧，time base `1/30`。本云端没有视频或 PNG 字节；以下逐帧值来自 `historical-request-source-contract.json`（19,714 bytes，SHA-256 `2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8`）和 Mac/Kaggle 重哈希报告。

| 输入 ID | 源帧 | PTS / 视频时间 | 原帧字节数 / SHA-256（只做来源） | 模型 PNG 字节数 / SHA-256 | 输入角色 |
|---|---:|---:|---|---|---|
| k01 | 0 | 0 / 0 s | 188,844 / `6133cfd6a5c7e43bf8c15969fd24736786b727e8d508fcc8f27ac4fa536773ec` | 182,917 / `d460c282a5fb02b5c98b36ad0b90f0840a037edfd9114fad4e2609a35a95fcc9` | HF past |
| k02 | 300 | 300 / 10 s | 200,574 / `e4c2c8aaedf45460a1414ada5dc3f5b3abca81dea5b785d2e405b4c0dd69b2c3` | 194,260 / `6f73af6b1dc20d8adc5ba01a5bc55246c5f3f1c8373c0e9ad55b3d3db0915091` | HF past |
| k03 | 450 | 450 / 15 s | 202,480 / `f553ab609b40c421076e7fb3735a14cdc3d7818a068616344feba7f223bcb8db` | 195,651 / `601c93866a4263af370480dcf0d79f672342b110d7cf4ab67b918ec5e2bf262d` | HF past |
| k04 | 510 | 510 / 17 s | 229,759 / `cc2f0bed03c5df2245392b58fd6da5a02b53a0e0e9a6acd6cb3b75750aec80a2` | 223,238 / `7fa67434a107f5086628a5175c74a9a7935ff2992b0ef10a4d1ffed0506645a8` | F current；HF current |

四张模型输入均为 640×480 PNG。派生图用完全不透明黑块 `(x=0, y=400, width=112, height=80)` 覆盖来源画面叠字；Mac 报告称矩形外 RGB 未变，原帧保留，没有 inpainting 或生成像素。原帧不是模型输入。当前输入图与视频字节不在云端，因此本表是已核验来源的身份清单，不声称本云端重算过 PNG SHA。

唯一首轮对照：F 仅 k04；HF 独立构造为 k01、k02、k03、k04，前三张标 `past`、k04 标 `current`。两次请求的 k04 必须是同一字节；HF 不能把过去历史无条件写成当前连接。H 不在本次请求预算内。

## 3. 独立 F/HF 请求与输出契约

两臂不共享对话历史；每臂都从相同的独立 system + user context 开始。提示词不在此复制，以免出现副本漂移；以下 hash 固定对应现有合同文件中的原 UTF-8 字符串：

- System prompt SHA-256：`4f08f7f8529e2badafa35cc80a66bf781b8f6bff376102556f1e762f0e273a57`。
- Common question SHA-256：`160e6d72526c226aa4f124abf7bf675840b2b74be467ddfb483a6ca8495f48d6`。
- F 条件角色文字 SHA-256：`a4a21adddece15794b7757af27b0bf7a58627cfb99bcfbc859cd917fcda4cc74`；组装 user text SHA-256：`d9a23f4a7085cc140e31b46c73602d01e8916675612b954ed19244d850f51e5c`。
- HF 条件角色文字 SHA-256：`1b4405192c094559fd1d0d12c32ba0eb182c32a0008f46e160b15ad851b0be8a`；组装 user text SHA-256：`a6bb4cd47d15dc3eaf5a55ea0d0d21401dc08952b4d3cfb2f2753bc5d3cf1df6`。
- Mac audit 的未发送 prospective serialized body 记录：F `301,251` bytes / SHA `abce46707c52ccff413f9a432bc87107b2a12fac25b09c527b20e7d73bf5f1cf`；HF `1,065,537` bytes / SHA `7cfea8f7a9977840a2a5efb3ababc4f0e9e167bad56f1f4a4b2fdbfa82e54a23`。这两个是 Mac 侧旧 sender 的预计算记录，不是 v5 实际已发请求 hash；本次无实际请求。v5 若运行会把实际 bytes/hash 写入私有 request 文件和 attempt journal。

参数候选固定为：model `Qwen3.5-9B-Q4_K_M`，temperature `0`，top_p `0.95`，top_k `40`，seed `1`，max_tokens `2048`，stream `false`，`enable_thinking=false`，`cache_prompt=false`。旧 source contract 未指定 top_p/top_k，但已归档 sender 和 v5 prepared runner 都明确写入 0.95/40；这是此次 admission 的候选值，运行前仍需从 execution config/server args 读回确认。旧 `request_plan.json` 中的 null 值不作为有效运行配置。

输出采用分别绑定 F/HF source schema 的 `json_schema`、`strict=true`、名称 `roboassem_task6_visual_state`。Mac 原 schema 字节现已随只读审计包进入云端并重哈希：F SHA `77240e2c680ae3d5b4ec6f28083c32bdb54eb8e757b47e0678b63ac40c751b4d`；HF SHA `45075bdcf0eb9144ddca918c74600901f4660f4b4a2d06b0601277b5b2193753`。这更新了旧 2026-10-11 交接中“原 schema bytes 未提供”的状态；旧回执保留不改。

v5 generation schema 会删除 `$schema`、`contains`、`minContains`、`maxContains`、`uniqueItems`；响应再由本地完整 Draft 2020-12 F/HF source schema 校验，并额外要求 current relations 中左右座各恰好一条。离线 v2 schema 文件是派生 validator，不是请求 schema 字节。backend 是否接受 `json_schema` 以及该 generation subset 未验证，故这仍是根需审阅的准入项。结果中 `state_write_allowed=false`，`SOP_completion`、`mechanical_lock`、`force_state` 恒为 `unknown`；F 的 history 空、identity continuity 为 `not_observed`；HF 历史只能引用 k01–k03，当前关系只能引用 k04。

顶层输出键为 `schema_version`、`condition`、`authority`、`state_write_allowed`、`identity_continuity`、`current_relations`、`historical_events`、`history_current_applicability`、`assumptions`、`SOP_completion`、`mechanical_lock`、`force_state`、`limitations`。`schema_version="1"`，`authority="development_state_estimation_only"`；关系 ID 固定为 `white_u_part`、`blue_seat_left`、`blue_seat_right`。关系外观限于 `visible_separation`、`contact_or_alignment`、`insertion_appearance`、`apparent_external_position`、`unknown`。所有输出仍只是 RGB 可见状态候选，不是物理锁紧、受力、SOP 完成或可写入的状态。

**v5 行为覆盖旧 prospective plan 中一处冲突。** `prospective-request-plan.json` 写着“schema error only recorded，不中止 HF”；但 v5 `make_response_parser` 对 schema/响应解析错误抛异常，`execute_pair` 将其作为 `processing_failed` 并停止序列。因此实际 v5 policy 是：F 发送、传输、解析或校验失败时 HF 不发送。只有 F 成功完成且验证通过才继续 HF；任何超时均不重试。这个决定在准入前显式记录，不按旧计划偷偷继续。

## 4. v5 runner 与预算

v5 archive 固定为 `task6-runner-patch-20261012-v5.zip`，30,926 bytes，SHA-256 `7bd01775520cc4b555f83b2edd1211430c51983cf1f07fe65475bab477de2f5c`。当前云端工作区有该文件，但 `.zip` 被 Git 忽略；Git 中可核验的源码副本来自 030b186 只读审计树：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `prepared-run_pair-crashsafe.py` | 29,513 | `baf2c20e0ef837fd095a6e1f87d893d05160206c2cfec9a593ffb51066a57e4b` |
| `runner_safety.py` | 19,874 | `d42e869daac52bd6261665f85122a747ef4011140e9498ea379ae504fbfd4075` |
| `prepared-execute-pair-bootstrap-crashsafe.py` | 7,412 | `967bc1386e4ff5eeb3019631e0a274fa9663dbdf36632a21e4a3e7e7e5df53d0` |
| crash-safety tests | 45,030 | `930993db6d4eb78905a8c5cf4e98109885df7df3e0bf85fc2fec7a2438377c5f` |

最新独立复审对 v5 archive 校验了 SHA，普通 Python 与 `python -O` 两组各 24/24，通过后建议推送。范围只含 mock HTTP/子进程、EOF guard、deadline/cancel、kill+reap 和 late-spawn 安全；没有 Kaggle、实际模型、真实 llama-server 或真实 60 秒请求验证。评审还发现 worker 未逐字段验证每个 message；`{"messages":[{}]}` 仍可到 mock `urlopen`，对方认为不阻断当时限定的 EOF 竞态范围。原 `uploaded-run_pair.py` SHA `9cb830588290d28b730f5dde1ce096d3e2ba43d6198bffcc79fe1ad2aabcf985` 不是 v5 runner，不能在本次研究中代替它。

预算由 v5 定义：F 后 HF，串行；最多两个 send intent，每臂至多一次，零重试；每次 HTTP wall-clock 60 秒，从持久化 send intent 计时，并在 deadline 前 50 ms 发起 sender cancel；最多 2,048 输出 tokens/臂、最多 4,096 合计。H=0。F 任一失败均终止 pair。超时时 server 可能已接收请求，状态记 `delivery_unknown`，attempt journal 阻止 replay；这不是服务端收据。启动服务健康等待上限 240 秒，构建命令上限 480 秒，这些 GPU 时间不包含在两次请求的 120 秒里，需另外取得相应额度/授权。

每臂 preflight 使用 tokenizer 实测文本 token 数 `T`，保守上界为 `T + 图像数×2048 + 2048 + 128 ≤ 16384`；因此 F 为 `T+4224`，HF 为 `T+10368`。先检查有效 context ≥16,384 并保存 `/props`，再做 F/HF 不生成的模板化/tokenize 预检。任一臂超过界限就停，不发任何 generation request。

## 5. 停止与产物留存

v5 `finally` 会 terminate 本地 `llama-server`，等待最多 20 秒；超时则 kill 并 wait。随后停止并 join GPU monitor，写 `server-stopped.json`（进程 return code、时间、send-intent 数；该数不是 server receipt），并将 `results/` 文件打包为 Kaggle working 目录中的 `task6-results.zip`。异常同时保留 `execution-error.json`。sender 子进程在 deadline 的结束状态单独写入 attempt/event 记录。

准入前须核对 `results/attempts.jsonl` 不存在，且没有其他 runner lock。已有 send intent 表示投递可能未知，任何新启动或重放都必须停止。v5 bootstrap 会校验 runner 与 safety module 字节 SHA、durably copy 后回读；bootstrap 的 `main()` 会随即启动 runner，因此它不是只读安装命令，根审阅和授权前不得执行。

一次真实运行若之后获准，结果清单至少应包括：`execution-config.json`、`server.log`、`gpu-memory.csv`、`backend-props.json`、`preflight-F/HF.json`、实际 `request-F/HF.json`、`attempts.jsonl`/event/failure ledger、原始 response bytes、parsed result、`execution-error.json`（如有）、`server-stopped.json`、`task6-results.zip`。request 文件含 base64 私有图像，原始响应也未审阅；这些只留在私有 Kaggle/授权私有存储，不提交公开 Git。下载或持久保存 zip 后，另记 zip 字节数与 SHA，并逐项核 manifest。确认本地 server 已停止之后，还须在 Kaggle UI 显式停止 session，再读回 session 为 stopped 的证据；关闭浏览器或 server-stopped 文件都不证明 GPU session 已释放。

## 6. 运行前的根审阅恢复顺序

1. **先看当前 Kaggle 会话，不启动新会话。** 记录当前会话是 running/stopped、GPU 型号、实际剩余额度/时长、此前任务是否仍占用 GPU；核准本次 build/startup 和最多 120 秒 generation 的总额度。若状态或额度无法确认，停在这里。
2. **只读检查现有数据，不调用下载代码。** 核对当前私有 input mount 的 `execution-input-contract.json`、四张 PNG 文件 size/SHA；在 Kaggle working 中只读核对两个 GGUF 的 size/SHA 与已有 `download-verification.json`。缺文件/不一致时立即停止，不运行 `uploaded-setup.py`，不重下权重，不换精度。云端 repo 中缺这四张 PNG、视频和 GGUF 是有意的私有数据边界；它们有旧 hash 记录，但不代表当前 Kaggle mount 仍在。
3. **核验 v5 部署字节。** 旧 Kaggle 私有数据源里报告上传的是 `uploaded-run_pair.py` 等旧版；prepared v5 文件记录为 `actual_uploaded_to_private_dataset=false`。如要继续，须先让 v5 文件按上表 SHA 到达私有输入 mount，并只读回核 SHA；不能运行旧 sender。不得执行 v5 bootstrap 的 `main()` 来“试一下”，它会启动 runner。
4. **核验 backend 二进制。** 只有当前 session、额度和已有源码树都已确认，且根批准必要 build 后，才可按 pinned source/build 配置编译。若源码不存在，不运行 setup 下载 tarball。成功后必须保存并核 `runtime-verification.json`，并用 `--version`、binary SHA 和 `--help` 确认 flags；缺失时不开始推理。
5. **启动本地 server 做无生成预检。** 保存 `/props`，要求 context ≥16,384；记录 F/HF 实测 token bound、schema generation subset、参数、runner/输入 SHA 于 `execution-config.json`。schema/API profile 或有效参数无法确认则停在零 generation。
6. **只在每一项授权及根审阅通过后执行 v5 一次。** F 先于 HF；每臂 60 秒、总计至多两次、无重试。超时或失败时不补发、不重放，delivery unknown 保留为 unknown。
7. **停止并回存。** 审核 `server-stopped.json` 与 send intent 数；将私有 `task6-results.zip` 另存并记录字节数/SHA；再在 UI 停止 Kaggle session 并确认 stopped。只发布脱敏 manifest/状态，不发布输入帧、base64 请求、私有响应或 notebook URL。

## 7. 当前具体缺项

- **缺少实际 backend 构建身份文件：** 当前没有 `runtime-verification.json` 的成功记录（version + binary SHA）；旧 notebook 转录显示 `Build 0`、`build_success_confirmed=false`。
- **缺少当前 Kaggle 会话证据：** 没有当前 GPU 型号、session running/stopped、可用 quota 或本次 compute/inference 授权读回。最近 GPU stop 只是用户报告，未独立核验。`notebook-observed-cells.json` 是 UI 回执的手工转录，完整 notebook export 和 notebook SHA 都不存在。
- **缺少 v5 Kaggle readback：** v5 源码和本地审阅包在云端 repo/Library 记录中，但 prepared runner/bootstrap 在 Mac audit 中明确记录未上传或执行；需要私有 mount 中的实际文件 SHA 读回。
- **当前挂载未复验：** Kaggle 曾有模型和四 PNG 的哈希重验记录，但本次没有实时挂载。本云端没有当前 Kaggle `execution-input-contract.json`、`download-verification.json`、四张 PNG、原视频或两个 GGUF 的活动拷贝；此前手工转录只证明当时有 cell 检查，不是这些文件的可下载副本或当前挂载回执。既有 hash 足以做只读比对，不构成重下载许可。
- **未解决配置差异：** `top_p/top_k` 在初始 text contract 未指定，现固定候选按 v5 0.95/40；旧 prospective plan 的“schema error 可继续 HF”与 v5 实际 abort 行为冲突，按 v5 abort 行为记录。generation schema 的 strict 支持仍未验证。
- **本次授权仍未核定：** `protocol.json` 中 compute/upload/download/inference flags 为 false；先前开发候选认可不等于当前 Kaggle compute 或 inference 的独立授权。

任何一项缺项都使状态保持 `preparation_only_blocked_for_execution`。此包不以其他实验的缺失材料作为 Task6 门槛，也不声称实际推理、60 秒运行、backend 兼容或识别效果已验证。
