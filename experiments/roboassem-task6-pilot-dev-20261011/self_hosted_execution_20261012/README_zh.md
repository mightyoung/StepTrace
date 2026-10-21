# RoboAssem Task 6 自托管运行包（2026-10-12）

**当前状态：`preparation_only_blocked_for_execution`。** 这是可移交的 CPU 准入、GPU 环境记录和结果回传包；此云端工作区没有视频、四张真实输入 PNG 或 GGUF 权重。本次没有构建 CUDA 后端、下载权重、启动服务器或调用模型。输入许可和可获取来源仍未核实，因此不得执行推理。

本包保留既有 Mac runner 审查快照，并在 `runner_sources/` 提供带额外 one-shot 产物守卫的版本化副本；请求与 F/HF 协议不变。运行默认最多两次请求，先 F 后 HF，串行、各 60 秒、零重试。任何 send intent 或其他运行产物都会使重放被阻止；超时保留为 `unknown`，不得补发。

## 内容

- `experiment-manifest.json`：输入、模型、backend、预算和候选环境锁。
- `execution-input-contract.json`：历史请求合同的逐字节副本，19,714 bytes，SHA-256 `2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8`。
- `environment-lock.json`、`requirements-runtime.lock.txt`：候选硬件/软件要求和 Python 包版本。
- `rights-attestation.template.json`、`provider-stop-attestation.template.json`：私有权利与资源停止记录模板。模板值无授权效力。
- `tools/task6_bundle.py`：CPU 输入/权限预检、运行记录、可选的本地 GPU 无生成预检、结果审核和回传清单工具。除 `gpu-preflight` 外的静态命令不联网、不启动 backend；`gpu-preflight` 只访问 loopback 预检端点，不下载、不发送 generation。
- `output-manifest.schema.json`：私有回传清单与脱敏摘要的 schema。
- `examples/synthetic/`：仅供校验工具的合成 schema fixture，不是视频帧、真实输出或模型结果。

## 公开来源能支持什么

- [固定 revision 的 GGUF 仓库页面](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5)列出模型文件，页面标示 Apache-2.0。此标签只涉及模型仓库许可，不授予视频、帧或衍生图像的使用/传输权。
- [Qwen3.5-9B 模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)提供模型来源信息；运行候选实际使用的是上方固定 revision 的 GGUF 与 projector 文件。
- [CUDA Toolkit 12.5.1 官方发布说明](https://docs.nvidia.com/cuda/archive/12.5.1/cuda-toolkit-release-notes/index.html)记录 CUDA 12.5 Update 1 的版本信息。本包候选要求 nvcc `release 12.5`、版本标记 `V12.5.82`，Linux driver floor 设为 `555.42.06`。
- [llama.cpp 固定源码 commit](https://github.com/ggml-org/llama.cpp/tree/8345f333951c661d166b00e6f9362e553768f292)是 backend 候选源码身份；尚无成功构建、二进制版本或 GPU 兼容性实测。
- 2026-10-14 的[来源与权利审计](../../../docs/research-handoff/audits/roboassem-task6-source-rights-audit-20261014.md)找到固定 RoboAssem 网站版本的视频来源候选和 3,708,210-byte 元数据；视频正文及 SHA-256 未获取，固定来源树也没有原样 k01–k04 PNG。网站级许可与这些媒体/衍生帧的适用范围仍未明确。此前 Mac 报告中的文件身份/hash 不是本机复验。只能从有权使用的私有来源取得**原有精确文件**；不得据此下载公开候选、重新解码/导出或补造 PNG。无法证明对准确输入及准确接收环境的使用和传输权限时就停在准入阶段。

## 固定输入与判断界限

| ID | 输入帧 | 原报告 SHA-256（PNG） | 条件用途 |
|---|---|---|---|
| k01 | 源帧 0 / 0 秒 | `d460c282a5fb02b5c98b36ad0b90f0840a037edfd9114fad4e2609a35a95fcc9` | HF 过去 |
| k02 | 源帧 300 / 10 秒 | `6f73af6b1dc20d8adc5ba01a5bc55246c5f3f1c8373c0e9ad55b3d3db0915091` | HF 过去 |
| k03 | 源帧 450 / 15 秒 | `601c93866a4263af370480dcf0d79f672342b110d7cf4ab67b918ec5e2bf262d` | HF 过去 |
| k04 | 源帧 510 / 17 秒 | `7fa67434a107f5086628a5175c74a9a7935ff2992b0ef10a4d1ffed0506645a8` | F 当前；HF 当前 |

源视频 `task6_m_1.mp4` 的 Mac 报告身份为 3,708,210 bytes、SHA-256 `49e048efeb14f8da2c9d6a2a23707f721b76d46bf76a4280cb2a03edcbfc3a06`。它只用于 provenance 核验，不进入模型请求。PNG 是报告所指的精确遮罩输入（黑色遮罩 x=0,y=400,w=112,h=80）；派生过程信息不足以声称可以字节相同地重建。

F 只含 k04。HF 将 k01/k02/k03 明确标作 past，并与相同字节的 k04 current 同次输入；两臂使用新建且相互独立的上下文。HF 中历史曾见的关系不能无条件写成当前连接；只能将其报告为历史条件并单独表达当前可见事实。遮挡、移除或物体身份变化的合成反例只测试软件的状态/unknown 处理，不能当真实负例、事件真值、准确率样本或证据增益。

现有材料只支持一条案例的输入身份与既有请求协议候选，不能支持接合真值、可见证据完整性、跨缺口身份恢复、SOP 完成、锁紧/受力结论、识别准确率、普适增益或安全性结论。遇到画面不可辨时应保留 `unknown`。F/HF 即使未来获得输出，也只是受限 RGB 状态候选，并非独立标注或物理真值。

## 在已获授权的自选 Linux CUDA 主机上运行 CPU 预检

可以在研究者已选定且可访问的 Linux x86_64 CUDA 主机上运行 CPU 预检。此文档不选择 provider、申请额度、连接主机或授权费用。不要把本仓库的云端路径当作 Mac/主机上的已保存文件。CPU `preflight` 子命令只使用 Python 标准库；输入完整并不等同于推理已获许可。

先在本机仓库根目录设置路径。`TASK6_INPUT_ROOT` 是私有输入 staging 目录，`TASK6_RUN_ROOT` 是私有运行目录；确保它们不在 Git 工作树内、权限仅限研究者，并在磁盘上有足够空间：

```bash
PKG="$PWD/experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012"
export TASK6_INPUT_ROOT="/private/task6-inputs"
export TASK6_RUN_ROOT="/private/task6-run"
mkdir -p "$TASK6_INPUT_ROOT" "$TASK6_RUN_ROOT"
```

把 package 内的 `execution-input-contract.json` 复制进私有输入目录；从有权使用的私有来源放入四张精确 PNG、同一来源视频、下面列出的三个 runner 源文件，以及私有 `rights-attestation.json` 和其证据文件。证据须能说明这组精确文件的取得/来源、遮罩帧衍生链、使用许可范围，以及向所选主机/provider 传输并在其上推理的权限；网页源码或模型许可不替代这些凭据。`evidence_file` 指向的文件必须位于私有输入根目录内；`evidence_bytes` 和 `evidence_sha256` 必须固定审阅过的同一证据文件，预检会比较其实际字节数和 SHA-256。不要把输入/证据提交 Git。三个 runner 文件位于本目录的 `runner_sources/`，预期身份在 manifest 中；原 `mac_runner_readonly_audit_20261012/sources/` 快照保持原样。

从模板制作 rights 文件时，需由权利人填写真实的 `rights_basis`、`license_id`、`evidence_reference`、`evidence_file`、带时区的 `expires_utc` 和获授权的 `provider_id`，并将 `synthetic_template_only` 显式改为 `false`；仅在证据明确覆盖这些行为时，才将 `authorization_status=authorized`、`inference_authorized=true`、`transfer_authorized=true`。`provider_id` 必须指明实际接收环境，空值、`UNSET`、`unknown` 和泛称 `self-hosted`/“自托管”不能通过。`expires_utc` 是准入声明的复核有效期，不代表推断出的媒体许可到期日；许可自身期限在证据中另行记录。公开再分发保持 `false`。用户认可“开发候选”不替代此逐项许可。不要填写示例文字冒充许可。

先运行 CPU-only inputs 阶段：

```bash
python3 "$PKG/tools/task6_bundle.py" preflight \
  --manifest "$PKG/experiment-manifest.json" \
  --input-root "$TASK6_INPUT_ROOT" \
  --run-root "$TASK6_RUN_ROOT" \
  --rights "$TASK6_INPUT_ROOT/rights-attestation.json" \
  --provider-id "<与私有授权记录完全一致的 provider ID>" \
  --phase inputs \
  --report "$TASK6_RUN_ROOT/preflight-inputs.json"
```

这项检查只读文件、逐项校验字节数/SHA、权限门、私有 runner 副本和 one-shot 结果根；`network_calls=0`、`generation_calls=0`。已有运行产物、无法检查的结果根或账本不完整时会阻断重跑。首次空 `results/` 可通过；`setup-evidence/` 可保存 GPU/CPU 预检材料，但不得放入请求、响应或运行账本。完整 GPU 预检输出中的 `server.log` 仅在 `setup-evidence/gpu-preflight/` 且同目录保留 `gpu-preflight.json` 与 `server-stopped.json` 时可用作预检证据；根目录正式 `server.log` 或缺少这两份回执的日志仍阻断启动。输入/许可缺失或不匹配会返回 exit code 2，这是预期的阻断信号；不要以新下载/重编码来绕过。该阶段的 `execution_ready` 总是 false，因为它没有核对 GPU runtime。

`phase=inputs` 检查的是输入合同、视频、四张 PNG、rights attestation/evidence、runner 副本和 one-shot 结果根；它刻意不查模型权重，因此可以在不下载 GGUF 时先验输入/权利。`data_rights` check 记录 attestation 与 `evidence_file` 的实际字节数/SHA。从 2026-10-21 起，v1 声明也必须提供正整数 `evidence_bytes` 和 64 位小写十六进制 `evidence_sha256`；旧声明缺项须重新审阅并补齐，不能自动升级为许可。此阶段 `execution_ready` 必然为 false，CLI 也会返回 exit 2；读取 JSON 报告中的 `input_ready` 和 `checks`，只有 `input_ready=true` 且无 blockers 才表示这个前置阶段通过。模型大小/SHA 仍在之后的 `phase=execution` 和 GPU 预检前强制核验。

## 模型文件、候选环境和资源上界

候选模型：`unsloth/Qwen3.5-9B-GGUF` revision `3885219b6810b007914f3a7950a8d1b469d598a5`。两个文件总计 6,602,227,488 bytes；以下 SHA 来自既有审计记录，本云端未下载或重算：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| `mmproj-BF16.gguf` | 921,705,024 | `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383` |

模型文件只在 execution 阶段和 GPU 预检前需要。若使用已经获准且可用的模型缓存，应先逐文件核对大小/SHA，并保存 pinned revision 与许可页面的私有来源记录。若权利人另行许可从固定 revision 获取模型，可使用精确 revision URL（不要加 token/凭证、改用浮动分支或使用 runner 自动下载）：

```bash
REV=3885219b6810b007914f3a7950a8d1b469d598a5
BASE="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/$REV"
curl --fail --location --retry 0 "$BASE/Qwen3.5-9B-Q4_K_M.gguf" \
  --output "$TASK6_RUN_ROOT/Qwen3.5-9B-Q4_K_M.gguf"
curl --fail --location --retry 0 "$BASE/mmproj-BF16.gguf" \
  --output "$TASK6_RUN_ROOT/mmproj-BF16.gguf"
sha256sum "$TASK6_RUN_ROOT/Qwen3.5-9B-Q4_K_M.gguf" "$TASK6_RUN_ROOT/mmproj-BF16.gguf"
```

若文件缺失、下载中断、许可证来源/标签不同、大小或 SHA 不符，停下；不得运行旧 `uploaded-setup.py` 或让 runner 下载/替换文件。不要将 Hugging Face token 加入命令历史或输入包。上面是未来操作者命令，不代表本次已下载或授权。

候选硬件/软件（见 `environment-lock.json`）：Linux x86_64；Python 3.12.14；`jsonschema` 4.26.0；Pillow 12.3.0；CUDA Toolkit 12.5 Update 1 (`nvcc` release 12.5 / marker `V12.5.82`)；driver 至少 555.42.06；CUDA0 compute capability ≥7.5；VRAM ≥16,384 MiB；主机 RAM ≥32,768 MiB；自由磁盘 ≥20 GiB。以上是未在 GPU 实机验证的候选阈值，不是运行容量或成本承诺。`requirements-runtime.lock.txt` 是版本清单，不含安装包 hash；CPU 预检不需安装它们，结果校验需要 jsonschema，v5 runner 还需要 Pillow。版本不吻合时停在预检，不在此云端安装。

若主机已有完全固定的源码 checkout，可确认 `git rev-parse HEAD` 等于 `8345f333951c661d166b00e6f9362e553768f292`。没有 checkout 时，只有在你已允许公开源码获取且主机不受网络限制的情况下，才可从固定来源检出该 commit；本任务没有执行 clone：

```bash
git clone --filter=blob:none --no-checkout https://github.com/ggml-org/llama.cpp.git "$TASK6_RUN_ROOT/llama.cpp"
git -C "$TASK6_RUN_ROOT/llama.cpp" checkout --detach 8345f333951c661d166b00e6f9362e553768f292
git -C "$TASK6_RUN_ROOT/llama.cpp" rev-parse HEAD
```

若不能从该来源获取准确 commit，停止。固定源码页面见 [llama.cpp commit](https://github.com/ggml-org/llama.cpp/tree/8345f333951c661d166b00e6f9362e553768f292)。确认源码后，未来 CUDA 构建命令候选为：

```bash
cmake -S "$TASK6_RUN_ROOT/llama.cpp" -B "$TASK6_RUN_ROOT/llama.cpp/build" \
  -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=ON -DCMAKE_BUILD_TYPE=Release
timeout 480s cmake --build "$TASK6_RUN_ROOT/llama.cpp/build" --target llama-server -j 4
```

不从脚本自动克隆/下载源码。若主机上没有 pinned checkout，则先停；本包未授权网络/成本或提供凭证，也未测试该 CUDA 编译。构建成功后只读记录 backend 与 GPU，不加载模型：

```bash
python3 "$PKG/tools/task6_bundle.py" record-runtime --run-root "$TASK6_RUN_ROOT"
```

该命令读取固定 commit、`llama-server --version/--help`、binary SHA、CMake/compiler/OS/Python/CUDA/driver/GPU0/RAM/磁盘资料；不会启动 server、打开权重或发送 generation。确认主机驱动与所有候选门槛后，才运行执行阶段 CPU gate：

```bash
python3 "$PKG/tools/task6_bundle.py" preflight \
  --manifest "$PKG/experiment-manifest.json" \
  --input-root "$TASK6_INPUT_ROOT" \
  --run-root "$TASK6_RUN_ROOT" \
  --rights "$TASK6_INPUT_ROOT/rights-attestation.json" \
  --provider-id "<同一 provider ID>" \
  --phase execution --require-gpu \
  --report "$TASK6_RUN_ROOT/preflight-execution.json"
```

exit code 必须为 0 才表示这些静态/资源门通过；这仍不验证模型兼容性、schema 支持、推理质量或数据事实。任何不通过都停在这里。

### GPU 端无生成预检

`record-runtime` 记录机器/软件/GPU 特征，但不采集 provider 或 VM/主机实例 ID。选定主机后、GPU 预检和运行前，在私有 one-shot 允许的 `results/setup-evidence/` 中建立 provider 停机记录副本：

```bash
mkdir -p "$TASK6_RUN_ROOT/results/setup-evidence"
cp "$PKG/provider-stop-attestation.template.json" \
  "$TASK6_RUN_ROOT/results/setup-evidence/provider-stop-attestation.json"
```

在此副本中填实际 `provider_id` 与本次 VM/主机的稳定 `instance_reference`，`provider_id` 必须与 rights attestation 和各 `--provider-id` 参数相同。云主机运行前保留 `status=unknown`；停止后再填 `status=stopped`、UTC 停止时间和 provider 控制台证据引用。自管主机用 `status=not_applicable`、`resource_type=self-managed-host`、`provider_id=self-managed-local`，并引用主机归属/边界记录。该身份由操作者提供，工具不会独立查询 provider 或将其自动绑定到 runtime 记录；之后需要人工核对。当前审计器在 `self-managed-host` 分支不强制 `instance_reference` 非空；仍应填写该字段，但不能把它当作机器核验结果。模板副本位于 `setup-evidence/`，不会触发 one-shot 重放守卫，且会进入私有回传 manifest 的文件哈希清单。

执行阶段 CPU gate 通过后，可先用独立命令做 GPU backend 的**无 generation**预检。这一步会加载本地 GGUF 到 CUDA0，运行固定的 `llama-server`，只访问 `127.0.0.1:8080` 的 `/health`、`/props`、`/apply-template`、`/tokenize`，为 F/HF 读回 context 与文本 token 数，按每张图 2,048 tokens 的上界计算 context bound，然后停止本地 server。它不提交 `/v1/chat/completions`，不发送图片/base64，不产生模型回答；不过会占用 GPU 并加载权重，操作者仍须先确认数据和主机授权/额度。

`--output-dir` 必须是一个新建的私有目录，且输入、运行、rights 和输出目录都不能位于 Git 工作树内。通过时 exit 0 且 `status=ready_for_operator_review`；这表示预检记录齐全、F/HF context 上界通过、server 已停止，留给操作者人工审核。失败时 exit 2，保留私有报告；不得转而直接运行 v5。该阶段未验证 strict JSON Schema generation 支持、内容正确性或识别质量。

```bash
python3 "$PKG/tools/task6_bundle.py" gpu-preflight \
  --manifest "$PKG/experiment-manifest.json" \
  --contract "$PKG/execution-input-contract.json" \
  --input-root "$TASK6_INPUT_ROOT" \
  --run-root "$TASK6_RUN_ROOT" \
  --rights "$TASK6_INPUT_ROOT/rights-attestation.json" \
  --provider-id "<同一授权 provider ID>" \
  --output-dir "$TASK6_RUN_ROOT/gpu-preflight-01"
```

审阅 `$TASK6_RUN_ROOT/gpu-preflight-01/gpu-preflight.json`、`backend-props.json`、`preflight-F.json`、`preflight-HF.json` 和 `server-stopped.json` 后，再决定是否进行有界 F/HF 生成。预检大致新增最多 340 秒的本地模型启动/endpoint/停止预算；之后 v5 还会重新加载模型再执行自己的预检和 generation。基础 v5 capped phases 上界 860 秒；预检加 v5 capped phases 合计候选上界 1,200 秒，模型/输入获取与主机 provision/返回传输不在内。需先获准这一完整预算；没有额度或数据权限时不执行 GPU 预检。

人工审核通过且准备运行前，把输入/执行阶段 CPU gate、runtime、GPU 预检、rights attestation 和其来源/权利证据归档到私有 results 子目录。`preflight-execution.json` 列出本次输入的实际字节数/SHA；复制 rights 文件及其引用的确切证据，可使 private return manifest 哈希审阅所用证据。不要复制视频、PNG 或模型权重。该目录不能包含在 Git 工作树中：

```bash
mkdir -p "$TASK6_RUN_ROOT/results/setup-evidence/gpu-preflight"
mkdir -p "$TASK6_RUN_ROOT/results/setup-evidence/input-rights"
cp "$TASK6_RUN_ROOT/runtime-verification.json" "$TASK6_RUN_ROOT/preflight-inputs.json" \
  "$TASK6_RUN_ROOT/preflight-execution.json" \
  "$TASK6_RUN_ROOT/results/setup-evidence/"
cp "$TASK6_INPUT_ROOT/rights-attestation.json" \
  "$TASK6_RUN_ROOT/results/setup-evidence/input-rights/"
# 将 rights-attestation.json 的 evidence_file 和其引用的来源/取得记录原样复制到 input-rights/，保留原文件名。
cp -a "$TASK6_RUN_ROOT/gpu-preflight-01/." \
  "$TASK6_RUN_ROOT/results/setup-evidence/gpu-preflight/"
```

## 未来运行协议（本云端未执行）

1. 独立确认数据使用和传输授权覆盖准确输入、准确 provider、推理目的及有效期；另行确认主机运行额度。没有 provider/权利证据时不执行。
2. 完成上面的独立 GPU 无生成预检，人工检查并保存私有报告；只有所有输入、资源、预检结果及 generation schema 风险都已接受，才将 `TASK6_INPUT_ROOT`、`TASK6_RUN_ROOT` 指向同一私有 staging 与运行目录，并执行准备好的 bootstrap：

   ```bash
   python3 "$TASK6_INPUT_ROOT/prepared-execute-pair-bootstrap-crashsafe.py"
   ```

   这一步会再次启动本地 llama-server 并实际发请求，只能在上述门槛和授权齐备后执行一次。它不是测试命令。runner 会绑定四张精确 PNG、固定 F/HF 序列和 runtime commit；版本化副本还会拒绝已存在运行产物的结果目录。runner 自身在 GPU 预检后没有人工暂停，所以人工复核必须先通过独立的 `gpu-preflight` 完成。
3. 固定参数是 temperature 0、top_p 0.95、top_k 40、seed 1、最多 2,048 output tokens、`enable_thinking=false`、`cache_prompt=false`。F 使用 k04；成功且完整通过校验后才发 HF，HF 为 k01/k02/k03 past + 同一 k04 current。两请求独立上下文、串行。
4. 最大 generation 总数 2，每臂最多 60 秒，无重试；另有 build 480 秒、v5 server readiness 240 秒、本地 shutdown 20 秒。v5 capped phases 合计上界 860 秒，独立 GPU 无生成预检另最多 340 秒；组合候选上界 1,200 秒（不含主机 provision、数据传输及模型获取）。超时可能已送达服务端，按 `unknown` 锁住重放；F 失败时 HF 不发。不要删除 `attempts.jsonl` 来重试。
5. v5 runner 会再次读取 `/props` 和 F/HF 无生成 token 上界，然后自动进入 F，不会暂停等人审核。有效 context 必须 ≥16,384；F/HF tokenizer 上界分别是文本 tokens + 4,224 / + 10,368。独立 GPU 预检与 v5 内部预检的参数/报告都要保留；strict schema generation 能力尚未实测，generation 前需明确接受这一未验证项，否则停止。

## 结果审核、停止证据和回传

v5 的私有 `results/` 可能含 `attempts.jsonl`、`attempt-events.jsonl`、`failures.jsonl`、`request-F/HF.json`（base64 图片）、`response-F/HF-raw.json`、`results.json`（模型输出）、`backend-props.json`、`preflight-F/HF.json`、`server.log`、`gpu-memory.csv`、`server-stopped.json`、错误记录和 `task6-results.zip`。这些均按私有数据处理；不要提交 Git、贴到 issue、发公开链接或放入公共云盘。

审核判据：只有 F 与 HF 都有完整响应、原始响应字节/hash 可对上、各自本地完整 schema 校验通过且审计无错误，pair 才是 `success`。F 在发 HF 前失败时为 `failed`，不能把单臂结果当作完整对照。`send_intent` 后没有可验证响应、响应丢失或不能判断送达时为 `unknown`，并保持 replay blocked；不得删除 attempt journal 或重试。仅当结果根除 `runner.lock` 外为空，或仅含 `setup-evidence/`，且没有执行产物时才是 `not_started`。账本缺失、空白、无法解析或不含有效 send-intent，遇到任何结果/运行产物都将阻断并记为 `unknown`；F/HF 产物无法与对应有效尝试关联时，该臂也保持 `unknown`。完整 GPU 预检目录中的 `server.log` 只有与 `gpu-preflight.json`、`server-stopped.json` 同在 `setup-evidence/gpu-preflight/` 才作为预检材料放行；其他位置或缺少回执的日志仍视为运行产物。`execution-error.json` 可能由发送后的异常写出，不能据它推断未发送。runner 与 bootstrap 在取得 one-shot 锁后也检查这些产物，阻止仅有孤立响应或执行错误时再次启动；保留原目录，不重建账本。任何审计错误都要保留原始私有证据并报告，不能修补/改写模型输出后称为成功。

本地 server 的 `server-stopped.json` 只能证明 runner 子进程退出。云端 VM/provider session 的停止需在其控制台/CLI 中单独停止，并保存私有 receipt/reference；更新预先放在 `results/setup-evidence/provider-stop-attestation.json` 的同一文件：云端填 `status=stopped`、准确 `instance_reference`、UTC `stopped_at_utc` 和 `evidence_reference`，且 `provider_id` 与 rights attestation/运行命令相同。验收工具只检查必需字段，不连接 provider、不独立验证其证据；返回报告中的 `verified_stopped` 仍是操作人提交停止证明的声明。若运行在自管主机且没有可停止的 provider session，使用 `status=not_applicable`、`resource_type=self-managed-host`、`provider_id=self-managed-local`、准确主机 `instance_reference` 和可审阅的边界说明引用。未知状态就保持 `unknown`。runner 自动创建的 ZIP 不含之后添加的停止证明，也不会递归收录上述 GPU setup-evidence 子目录；回传时以完整私有 results 目录和 return manifest 为准。若需要单文件传输，可在审计通过后从完整私有 results 树新建 ZIP 并记录 SHA；原 runner ZIP 保留原状，不要声称其含后来加入的证据。

```bash
TASK6_RETURN_DIR="$(mktemp -d "$TASK6_RUN_ROOT/return.XXXXXX")"
if python3 "$PKG/tools/task6_bundle.py" validate-results \
  --results-dir "$TASK6_RUN_ROOT/results" \
  --contract "$PKG/execution-input-contract.json" \
  --stop-attestation "$TASK6_RUN_ROOT/results/setup-evidence/provider-stop-attestation.json" \
  > "$TASK6_RETURN_DIR/validate-results.out" 2>&1; then
  VALIDATE_RESULTS_EXIT=0
else
  VALIDATE_RESULTS_EXIT=$?
fi
printf '%s\n' "$VALIDATE_RESULTS_EXIT" > "$TASK6_RETURN_DIR/validate-results.exit-code"
if python3 "$PKG/tools/task6_bundle.py" write-return \
  --results-dir "$TASK6_RUN_ROOT/results" \
  --contract "$PKG/execution-input-contract.json" \
  --stop-attestation "$TASK6_RUN_ROOT/results/setup-evidence/provider-stop-attestation.json" \
  --return-dir "$TASK6_RETURN_DIR" \
  > "$TASK6_RETURN_DIR/write-return.out" 2>&1; then
  WRITE_RETURN_EXIT=0
else
  WRITE_RETURN_EXIT=$?
fi
printf '%s\n' "$WRITE_RETURN_EXIT" > "$TASK6_RETURN_DIR/write-return.exit-code"
RETURN_EXIT=0
if [ "$VALIDATE_RESULTS_EXIT" -ne 0 ] || [ "$WRITE_RETURN_EXIT" -ne 0 ]; then
  RETURN_EXIT=1
fi
printf '%s\n' "$RETURN_EXIT" > "$TASK6_RETURN_DIR/return.exit-code"
exit "$RETURN_EXIT"
```

即使 `validate-results` exit 非零或报告 `failed`/`unknown`，以上 `if` 写法仍会继续尝试 `write-return`。总退出码 `return.exit-code` 在两子命令都为 0 时为 0，否则为 1；两个原始子码分别留在 `validate-results.exit-code` 和 `write-return.exit-code`，命令输出留在对应 `.out` 文件。若 stop attestation 缺失/JSON 无效，`write-return` 可能在写 manifest 前失败；此时保留 return 目录诊断、完整 `results/` 和其未知状态，不得伪造 stop 证明，也不能保证生成 manifest。只有补到真实 stop 证据后，才可在另一个新 return 目录重跑只读审计/回传命令；不重跑模型或覆盖旧失败记录。将 return 目录及完整私有 `results/` 交给审阅者。每次 return 使用新的目录。两子命令都 exit 0 仅表示 audit 结构和来源证据无内部矛盾，仍需看 `inference_status`；`failed` 或 `unknown` 不是成功实验。`inference_status=success` 只代表 F/HF 返回字节与内容校验通过，不证明实际 generation 使用了 GPU；`gpu_execution_status` 固定为 `not_verified`，因为结果审计器不验证 GPU 执行期证据。失败和 unknown 原始产物应保留并作为阻断上报，不要重试。

若需将完整私有结果树打成一个传输文件（包括嵌套 setup-evidence 与 provider stop attestation），可在审计通过后运行以下本地归档命令；它拒绝 symlink，只写入当前私有 results 内容并打印归档大小/SHA：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib, os, zipfile

run_root = Path(os.environ["TASK6_RUN_ROOT"]).resolve()
results = run_root / "results"
archive_path = run_root / "task6-results-full.zip"
if archive_path.exists():
    raise SystemExit("archive exists; preserve it and choose a new name")
with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(results.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"refusing symlink: {path}")
        if path.is_file():
            archive.write(path, path.relative_to(results).as_posix())
hasher = hashlib.sha256()
with archive_path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        hasher.update(chunk)
digest = hasher.hexdigest()
print(archive_path.name, archive_path.stat().st_size, digest)
PY
```

保留 `private-return-manifest.json`、`redacted-summary.json`、私有 `task6-results.zip` 及其逐字节 SHA 记录。`output-manifest.schema.json` 区分私有 manifest 与仅含状态计数的脱敏摘要；摘要默认 `publication_allowed=false`，必须由权利人另审。摘要不含模型输出、私有路径或输入/响应 hash。审阅后如要公开，只提交明确批准的脱敏摘要；原始请求、base64、视频/帧、模型权重、模型输出、日志、完整 notebook、私有证据和授权文本永不提交。

向研究仓库回传时，先从当前分支更新，再新建任务增量分支；只加入获准的代码/说明/脱敏摘要，并在提交前检查 Git 状态：

```bash
git switch task/roboassem-task6-execution-admission-20261012
git pull --ff-only
git switch -c task/roboassem-task6-self-hosted-return-YYYYMMDD
# 只复制经复核获准的脱敏返回物；不要复制私有 results/ 或 task6-results.zip
git status --short
git diff --check
git add <明确列出的公开文件>
git commit -m "Record Task 6 self-hosted result receipt"
git push -u origin task/roboassem-task6-self-hosted-return-YYYYMMDD
git rev-parse HEAD
git ls-remote origin refs/heads/task/roboassem-task6-self-hosted-return-YYYYMMDD
```

仅当本地完整 commit SHA 与 `git ls-remote` 读回一致时，才报告远端已收到该分支。**本包的操作示例不是当前已运行/已推理结果，也没有为之后的数据公开授权。**

## 当前阻断项与证据分类

| 项目 | 状态 | 证据来源/限制 |
|---|---|---|
| 视频来源元数据与视频/PNG 权利 | 仅发现固定版本来源候选，执行阻断 | 视频正文/hash 未获取；固定树无原样 PNG；媒体许可范围不明，需要精确私有文件及权利凭据 |
| 原视频与精确 masked PNG 字节 | 云端未提供 | PTS/大小/SHA 是先前 Mac 报告，不能当成本地复验或路径 |
| GGUF 权重与 projector | 云端未下载 | 固定 revision、文件大小/SHA 是既有审计记录；下载后需操作者校验 |
| GPU/CUDA/backend 构建 | 未实测 | 阈值为候选；没有当前 GPU、driver、实际二进制或 schema 可用性读回 |
| F/HF 模型调用与输出 | 0 次 | 没有识别准确率、增益或普适性结果 |
| runner v5 | 只读复用 | 源码有旧的 mock crash-safety 审阅；此包未改变其文件 |
| 示例 JSON 与单元测试 fixture | 合成 | 仅验证代码和 schema，不属于真实负例、模型输出或研究证据 |

优先继续可独立完成的 CPU 代码/manifest 检查。只要输入许可或精确文件缺失，GPU 构建和推理仍保持关闭；不要把任何 cloud workspace 路径描述成 Mac 已保存材料。

## 2026-10-21 准入增量

[权利、接收边界与固定后端资格计划](../../../docs/research-handoff/progress/20261021-task6-rights-evidence-binding.md)记录本次 synthetic 回归与待决事项。证据哈希绑定只验证文件一致性；`data_rights=verified` 仍表示声明字段和文件身份通过机械检查，不是工具独立确认权利人身份、许可适用范围或真实主机归属。

## 离线资格记录检查（2026-10-21 后续）

[固定后端资格协议与最小私有字段](QUALIFICATION_PROTOCOL_zh.md)和 `qualification-packet.template.json` 支持先填明确事实、保留未知。`tools/task6_qualification.py audit` 检查字段/固定配置/阶段身份关联；`schema-check` 只校验命名 JSON。两者不读取媒体/权重、不调用模型或联网，始终保持 `execution_ready=false`；metadata 完整和本地 schema 通过都不代表后端合格或底层媒体许可已获验证。
