# StepTrace Task6：输入来源与许可证据核对

核对时间：2026-10-14 UTC。仓库基线：`mightyoung/StepTrace@f4172e870430b21fe5ea89727eeab76864f052d1`。

## 结论

1. **找到第一方公开视频来源候选**：RoboAssem 网站的固定源码版本确实引用 `static/videos/task6_m_1.mp4`；GitHub 文件元数据报 3,708,210 bytes，与 StepTrace 输入合同一致。此次未获取视频字节，不能确认其 SHA-256 等于合同，也不能把“找到来源候选”写成“精确输入已验证”。
2. **媒体许可仍需要范围澄清**：同一固定源码版本的根 LICENSE 为 MIT，网页页脚声明网站使用 CC BY-SA 4.0。两项声明的媒体适用范围、相互关系、遮罩 PNG 的权利链没有明确说明。不能断言两者法律冲突，也不能认定媒体绝无许可；现有公开证据不足以替代该运行包要求的精确输入与指定接收环境权利凭据。
3. **模型许可链更明确**：固定 Unsloth revision 的 README 声明 Apache-2.0，并链接 Qwen 的 LICENSE；已把链接目标正文定位到固定 commit。GGUF 与 projector 的公开 SHA-256 均与 manifest 一致。这些是模型证据，不是视频/PNG 的许可。
4. **用户服务器自托管和外部模型服务必须分开**：模型可自托管不等于媒体可传给第三方。自有机器上的 loopback 推理不意味着发送给 Hugging Face、Unsloth、Qwen 或 OpenAI；租用 GPU 主机仍涉及具体托管方的数据接收。尚未选定的 provider 不能用泛称覆盖。

本报告是来源与证据缺口核对，不是法律确定性意见。未修改仓库，未运行实验，未登录、接受条款、下载媒体或模型权重，未向模型或计算 provider 发送输入。

## 1. 固定实验合同

核对材料：

- [执行准备回执](https://github.com/mightyoung/StepTrace/blob/f4172e870430b21fe5ea89727eeab76864f052d1/docs/research-handoff/receipts/roboassem-task6-self-hosted-execution-20261013.json)
- [自托管说明](https://github.com/mightyoung/StepTrace/blob/f4172e870430b21fe5ea89727eeab76864f052d1/experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/README_zh.md)
- [experiment-manifest.json](https://github.com/mightyoung/StepTrace/blob/f4172e870430b21fe5ea89727eeab76864f052d1/experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/experiment-manifest.json)
- [execution-input-contract.json](https://github.com/mightyoung/StepTrace/blob/f4172e870430b21fe5ea89727eeab76864f052d1/experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/execution-input-contract.json)

状态仍为 `preparation_only_blocked_for_execution`。输入合同 19,714 bytes，SHA-256 `2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8`。

| 文件 | bytes | 合同 SHA-256 | 用途 |
|---|---:|---|---|
| task6_m_1.mp4 | 3,708,210 | `49e048efeb14f8da2c9d6a2a23707f721b76d46bf76a4280cb2a03edcbfc3a06` | 私有 provenance，不进入模型请求 |
| k01.png | 182,917 | `d460c282a5fb02b5c98b36ad0b90f0840a037edfd9114fad4e2609a35a95fcc9` | 源帧 0、0 秒；HF past |
| k02.png | 194,260 | `6f73af6b1dc20d8adc5ba01a5bc55246c5f3f1c8373c0e9ad55b3d3db0915091` | 源帧 300、10 秒；HF past |
| k03.png | 195,651 | `601c93866a4263af370480dcf0d79f672342b110d7cf4ab67b918ec5e2bf262d` | 源帧 450、15 秒；HF past |
| k04.png | 223,238 | `7fa67434a107f5086628a5175c74a9a7935ff2992b0ef10a4d1ffed0506645a8` | 源帧 510、17 秒；F 与 HF current |

四张 PNG 都是 640×480、黑色矩形遮罩 `x=0,y=400,w=112,h=80` 的精确衍生文件。合同里的 `frame-01.png` 至 `frame-04.png` 是另一组原始帧身份，不能代替 k01–k04。当前 package 明确 `regenerate_if_missing=false`；不得重新解码、导出或重编码来假装字节相同。此次未读取任何图像像素，也未重算这些输入 hash。

## 2. 第一方媒体来源：新增的可确认事实

第一方网站：[RoboAssem](https://roboassem.github.io/)。网站源码仓库读取时的 `main` 为 `f2a1519def202dce63056204912d801378e15235`，提交日期 2025-09-18。

- [固定 index.html 第 583、587 行](https://github.com/RoboAssem/RoboAssem.github.io/blob/f2a1519def202dce63056204912d801378e15235/index.html#L583-L587) 两次引用 `./static/videos/task6_m_1.mp4`，位于 U-peg Reassembly 的主相机演示位置。
- [固定视频文件页面](https://github.com/RoboAssem/RoboAssem.github.io/blob/f2a1519def202dce63056204912d801378e15235/static/videos/task6_m_1.mp4)
- [固定目录 API 元数据](https://api.github.com/repos/RoboAssem/RoboAssem.github.io/contents/static/videos?ref=f2a1519def202dce63056204912d801378e15235) 返回该文件大小 3,708,210 bytes、Git blob SHA `5de43c39047ec72d2f9b0e9357fd42461d87a8a5`，并给出精确下载 URL：`https://raw.githubusercontent.com/RoboAssem/RoboAssem.github.io/f2a1519def202dce63056204912d801378e15235/static/videos/task6_m_1.mp4`。
- 此下载 URL **仅由官方 API 元数据确认**；未请求媒体 body，未验证最终下载响应、播放、SHA-256 或与 Mac 文件逐字节一致。Git blob SHA 不是文件 SHA-256，二者不可混用。
- [完整递归树元数据](https://api.github.com/repos/RoboAssem/RoboAssem.github.io/git/trees/f2a1519def202dce63056204912d801378e15235?recursive=1) 返回 `truncated=false`，297 个条目，没有 `.png` 文件；因此此固定版本没有提供 k01–k04 原样 PNG。此结论仅限所查固定版本，不能扩大成互联网不存在这些文件。

可修正的叙述仅是：“此前回执未核实公开 URL；本次已发现第一方固定版本的视频来源候选及元数据。”不能覆盖旧回执，也不能把 `media_use_and_transfer_rights_verified` 改成 true。

## 3. 媒体许可：确切声明与尚未解决的边界

### 已观察到

- [同版本根 LICENSE](https://github.com/RoboAssem/RoboAssem.github.io/blob/f2a1519def202dce63056204912d801378e15235/LICENSE)：MIT，copyright 2025 WuDuidi，授权对象文字为软件及相关文档；要求保留版权及许可声明。Git blob `3c782ba63a189adbdda4f8dd7a394ca6a611e8a3`。
- [同版本网页第 899–912 行](https://github.com/RoboAssem/RoboAssem.github.io/blob/f2a1519def202dce63056204912d801378e15235/index.html#L899-L912)：网站声明 CC BY-SA 4.0，并注明页面改自 Nerfies。该声明是第一方可观察事实；不能自行假定仅限模板，亦不能自行假定覆盖所有媒体与第三方权利。
- [README](https://github.com/RoboAssem/RoboAssem.github.io/blob/f2a1519def202dce63056204912d801378e15235/README.md) 只有项目名。检查到的完整树与网站正文未见独立 dataset/media 使用条款或 Task6 逐文件授权说明。

### 待核

1. `task6_m_1.mp4` 及其帧应依据哪一项许可，许可方是否有权涵盖相关媒体内容。
2. 用户的原视频与四张既有遮罩 PNG 从何处获得，取得时的条款、衍生链及精确文件身份。
3. 在指定环境上的本地研究推理、文件复制/传输是否处于适用授权范围；外部托管商/API 接收输入时还应检查其数据使用、保留、训练和再许可条款。
4. 后续若公开图片、视频或改编素材，相关署名、修改标记与 ShareAlike 等义务如何适用。不要把纯模型输出是否构成改编作品作未经审核的确定性结论。

[CC BY-SA 4.0 正文](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en) 可以解释被授权材料的复制、改编、分享、署名及同方式分享条件，以及隐私/人格等未一并授权的权利；它本身不能证明此确切视频或四张 PNG 已被许可方纳入授权。此次没有判定某项法定例外是否适用。

## 4. 模型来源与许可链

实际候选固定为 `unsloth/Qwen3.5-9B-GGUF@3885219b6810b007914f3a7950a8d1b469d598a5`，不是任意 Qwen API、其他量化或浮动 main。

| 文件 | manifest bytes | 公开文件页面 SHA-256 |
|---|---:|---|
| Qwen3.5-9B-Q4_K_M.gguf | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| mmproj-BF16.gguf | 921,705,024 | `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383` |

- [固定 README](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/3885219b6810b007914f3a7950a8d1b469d598a5/README.md) 的元数据声明 `license: apache-2.0`、`base_model: Qwen/Qwen3.5-9B`，并链接 `https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/LICENSE`。注意该 license_link 本身是浮动 main。
- 本次把其目标 LICENSE 内容固定为 [Qwen commit ef3d031a90d340a92d71f83ec17d054e100ce713 的 LICENSE](https://huggingface.co/Qwen/Qwen3.5-9B/blob/ef3d031a90d340a92d71f83ec17d054e100ce713/LICENSE)，正文是 Apache License 2.0，包含 Alibaba Cloud 2026 版权声明。这只固定许可文本证据，不声称此 commit 就是 Unsloth 量化时使用的精确上游权重版本。
- [GGUF 文件页面](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf) 与 [projector 文件页面](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/3885219b6810b007914f3a7950a8d1b469d598a5/mmproj-BF16.gguf) 的 SHA-256 与 manifest 一致。[GGUF 的文字 LFS pointer](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/raw/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf) 还确认精确大小。projector 的 pointer 抓取未成功；其精确 bytes 仍来自 manifest，公开页面显示约 922 MB。
- 仅访问网页与文字 pointer，未获取任一权重 body；公开 URL/元数据存在不等于未来网络可达、下载完成或本地 hash 已核验。

**使用与再分发应区分**：Apache-2.0 的许可链支持把本地模型使用作为可行候选；再分发模型或修改版本须遵守许可文本、修改标记及适用通知等条件。它不要求把每次本地推理等同于公开分发，也不对 Task6 输入授予任何权利。模型许可允许某行为与用户是否授权本次下载、上传、花费或公开发布是不同问题。

## 5. 五项不能混同的核对

| 项目 | 当前证据结论 |
|---|---|
| 下载来源/可达性 | 视频有第一方固定来源元数据；四 PNG 没有公开原样来源；模型页面和 SHA 可读。所有二进制下载均未实测 |
| 媒体使用许可 | 找到网站级 MIT/CC BY-SA 声明，精确媒体与衍生 PNG 的适用范围待核 |
| 用户自有服务器上的本地推理 | 模型许可链可查；媒体权利、主机控制边界、运行授权与精确字节仍须分别核对 |
| 向第三方 provider 传输 | 尚无具体接收方及覆盖该传输的数据权利凭据；不能由模型许可或公开可见推导 |
| 权重再分发 | 存在 Apache-2.0 声明及正文证据；需落实再分发条件和用户对该发布行为的授权；当前任务未授权再分发 |

自托管方案的 API 为用户所选主机上的 `127.0.0.1:8080`；协议兼容 OpenAI 不意味着调用 OpenAI 服务。把媒体先上传到租用主机，再发 loopback，仍需记录主机托管方这一接收环境。不能把 host/provider 名称填成“自托管”来掩盖实际第三方。

## 6. 最小需要补齐的材料

1. **原有精确文件所在的获授权私有位置**：视频加 k01–k04，以及来源/取得记录；由获授权执行环境核对上表大小与 SHA-256。无需重新取得或重导出四张图，也无需解锁无关电脑。
2. **与这些材料相联系的权利凭据**：能界定上述网站声明是否覆盖该视频与既有遮罩帧的第一方条款、原始取得约定或权利人说明。若公开许可适用范围能明确，不应额外凭空要求一份新的专门许可；若仍不明，则需说明提供者/权利人对该范围的确认。普通“可以跑实验”不能替代底层数据权利。
3. **确切的运行/接收环境**：服务器归属和控制方、实际 host/provider ID；若用第三方托管或外部模型 API，需把该接收方及相应的数据使用范围一并核对。纯自有环境与外部 API 不合并授权。

模型侧无需用户重复寻找泛用 Apache 文本；上述固定许可链已经找到。后续真正使用时需把来源/许可证据存入私有运行记录，并核对本地两文件 hash。当前 package 的 [rights 模板](https://github.com/mightyoung/StepTrace/blob/f4172e870430b21fe5ea89727eeab76864f052d1/experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/rights-attestation.template.json) 默认未授权，不能仅凭本报告把其布尔值改成 true；其中有效期若来自运行准入策略，应与许可自身的期限区分，不能伪造许可到期日。

**停止条件**：在精确文件与上述权利/接收边界补齐前，保留 preparation-only；不启动 GPU 预检或推理，也不通过换模型、换媒体或重编码来跨过既有 gate。
