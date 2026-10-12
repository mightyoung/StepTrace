# Task6 Mac Kaggle 发送代码只读审查快照

此目录归档本机已上传和已准备的代码，不是冻结版，也不是运行结果。正式 F/HF 发送数为 0/0；没有原始模型响应。禁止把此归档当作可直接执行的已验收 runner。

`sources/uploaded-run_pair.py` 是私有四图数据集中的原发送代码。`sources/prepared-context-run_pair.py` 和 `sources/prepared-execute-pair-bootstrap.py` 仅在 Mac 准备，未提交到 Notebook 执行。源码字节、完整历史纯文本请求合同及两个 schema 的 SHA256 见 `source-manifest.json`。不包含图像、视频、模型权重、base64 载荷、认证信息或私有服务端点；源码仅使用 Kaggle 沙箱路径和 localhost。

本机没有导出的当前 Kaggle `.ipynb`。`notebook-observed-cells.json` 只转录此前 UI 工具实际读回的两格代码及其状态，不是 Notebook 导出；不能给整个 Notebook 提供源码 SHA。此前云端 `kaggle_private_draft.ipynb` 只有离线 checker，不是这些实际部署单元格。

## 实际状态

四张遮罩 PNG 在 Kaggle CPU 和恢复后的 T4 会话均重新核过大小与 SHA256；两个固定权重已下载、核验并在恢复后的 T4 中再次重哈希，没有重复下载。官方 llama.cpp 固定提交为 `8345f333951c661d166b00e6f9362e553768f292`，模型修订为 `3885219b6810b007914f3a7950a8d1b469d598a5`。CPU 构建因缺少 nvcc 失败；T4 构建只读到 `Build 0`、24.251 秒单元格耗时，未读到成功记录，所以构建成功仍未证实。

随后 Mac 锁屏。最新停止状态来自用户报告“已手动停止”，尚未由工具独立核验；旧的 `Session stopped` 证据属于更早的一次会话关闭，不得当作这次独立验证。输入、权重及提示合同保持原样。

## 两次发送计划与现有保护

计划顺序 F → HF、独立 messages、串行、每臂最多一尝试、HTTP 超时 60 秒、零重试、每臂最多输出 2048 token。F 输入为 k04；HF 为 k01/k02/k03/k04，当前 k04 base64 字节串完全相同。固定提示和角色文字来自历史合同。运行时新增显式 top_p=0.95、top_k=40、cache_prompt=false；temperature=0、seed=1、stream=false、enable_thinking=false。这些是拟发送值，未在真实后端验证成有效值。

原 runner 第 79 行先写完整请求 JSON，第 81 行再关闭追加写入的 `attempts.jsonl` 文件，随后第 84 行才调用 `/v1/chat/completions`。因此**有发送前文件记账，但没有经过崩溃持久性验证**。账目仅有 condition、attempt、request_sha256、start_epoch，没有 request UUID 或明确 pending/sent/response_unknown 状态。存在账目文件时原 runner 第 5 行拒绝再运行；但检查不是原子锁，也未 fsync 文件或目录，不能宣称跨进程或断电后严格至多一次。

成功 HTTP 返回先存原始响应字节再解析；异常记录 repr(error) 后抛出，不继续下一臂、不重试。F 发生网络/解析/finish reason 错误时 HF 不会执行，所以代码保证的是最多两次，不能保证恰好两次。schema 错误仅记为 schema_valid=false，不触发异常，因此可能继续 HF；错误仍须作为失败保留。HTTPError 响应体未保存；响应全部 read 完成前断线也不保留部分字节。

finally 终止 llama-server、记录请求账目行数并打包结果。它**不会关闭 Kaggle GPU 会话**；仍依赖 UI 手动停止。浏览器断线或锁屏不会可靠取消后台单元格；恢复时必须先读账目和会话，状态不明不得重发。

## 不可冻结的具体问题（未修复）

1. **准备版路径错误**：bootstrap 将派生 runner 放到 `/kaggle/working/steptrace-task6`；runner 用 `__file__.parent` 找合同和四图，而它们在私有输入挂载目录。没有复制操作。该版本会在发送前找不到合同/PNG，不能直接执行。
2. **发送记账耐久性和互斥不足**：无 fsync、原子抢占锁、全局唯一请求标识、送达不明状态及 Mac 镜像记账，Kaggle Files only 也不是每次发送前的已验收持久化证明。
3. **完整执行配置未绑定**：原 runner 没有强制比对合同源码/提示 hash，也没有在启动服务前重新核模型文件 hash 和实际二进制 hash。上传输入校验和 Notebook 预检不能替代完整执行冻结。
4. **有效后端参数未核实**：准备版只新增 `/props` 的 n_ctx/n_ctx_per_seq >=16384 检查，提取的 settings 未用来校验 seed/temperature/top_p/top_k/thinking。实际后端未验证，也没有冻结的已生成完整请求 hash。
5. **无截断仍是部分检查**：预检只 tokenize 去掉图片后的文本，加上每图 2048 token 和 128 余量；不是实际完整多模态 tokenization。响应无 truncated 字段时检查默认 False，只在记录中写 inspect_server_log，不能把字段缺失当无截断证据。
6. **生成 schema 是子集**：移除 contains/minContains/maxContains/uniqueItems/$schema 后生成，再用完整原 schema 离线检验，并另要求左右座各一条关系。必须区分生成时约束与离线验收，不冒称后端完整 strict 支持。该转换已明确记录，但尚无后端实测。
7. **部署成功与会话释放未闭环**：没有确认 BUILD_VERIFIED、实际模型加载、真实响应或这次 GPU 独立停止；未导出当前真实 Notebook 源码。

这些发现来自静态阅读和此前真实 UI 状态，不是模型运行失败，也不支持准确率或方法增益结论。本次不修改 Notebook、不修复 runner、不启动 GPU/推理，仅供父线程和云审查核对。
