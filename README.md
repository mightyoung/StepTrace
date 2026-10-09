# StepTrace

面向单人视频的实时 SOP 步骤执行与接续研究：根据当前可见证据识别正在执行的步骤、已确认进度和下一步接续，证据不足时保留 `unknown`，不把后来的证据倒填成过去已经确认的步骤。

## 当前状态

这是从 `display-project` 提取的源码发布快照，内部 Python 包仍叫 `display_project`。现有源码包含视频/事件渲染、YOLO 与 Grounding DINO 适配、SAM2 掩码、跟踪、确定性 SOP 状态机、任务配置、证据证书、风险校准和独立的 VLM 状态估计模块。模块存在不代表端到端实时性能或正式科研效果已经验证。

确定性执行路径只能由获授权的检测/几何证据推进，VLM 审计不能单独推进状态。`state_estimation_only` 路径保留观察和估计的区分，不授予生产控制权限。证据不足、缺少输入或失效时应拒绝推进；跳步与逆序应报告偏差。未来观察不能补写过去的确认记录。

项目目标是过程步骤监测与接续，不据视频直接声称成品质检、装配质量合格或科研验证完成。旧研究文档中的门禁、计划和开发结果未随本发布包成为正式证据。

## 安装与自检

需要 Python 3.10 或更新版本。

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m display_project.cli doctor
python -m pytest -q tests/test_state_machine.py tests/test_vlm_state_memory.py tests/test_vlm_evidence_window.py tests/test_video_render.py tests/test_task_config_compiler.py
```

自检报告 Python、OpenCV、FFmpeg 与 FFprobe。测试验证软件行为和合成视频渲染，不能当作真实装配效果验证。

## 无外部数据的合成渲染演示

```sh
python scripts/create_demo_assets.py --output-dir data/meccano/subset --seconds 2
python -m display_project.cli validate-events data/meccano/subset/events.json
python -m display_project.cli baseline --config configs/demo.yaml
```

生成的 `outputs/baseline-demo.mp4` 是带预设事件的合成视频演示，其步骤和框由脚本生成，不能作为模型推理、SOP 准确率或真实完成证据。原始视频与生成输出均被 Git 忽略。

真实检测入口包括 `scripts/run_yolo_real_sop_detection_zh.py`；运行需要自行准备并登记视频、模型、配置、字体与相应依赖。先使用 `--help` 检查所需参数，不以缺失资产冒充真实运行完成。

## 文件范围

- `src/display_project/`：运行时、状态机、视频、检测/跟踪、证据和数据转换。
- `scripts/`：选取的本地 Python 工具；临时、远端操作与论文生成脚本未发布。
- `configs/`：小型任务与开发配置；远端 G3 admission 配置未发布。
- `tests/`：软件测试源码。部分历史测试依赖未发布的冻结研究文件、原始数据或研究证据，完整套件不是本发布包的通过承诺。
- `docs/`：数据说明、发布与验证记录。

发布副本内服务器和个人路径改为占位路径；其历史实验脚本需要重新配置。原始目录、冻结文件与历史资产未改动。

## 数据与后续验证

数据、模型、个人内容、会话、原始输出和论文全文不随仓库发布。参见 [外部资产说明](docs/external-assets.md) 和 [发布文件清单](SOURCE_MANIFEST.json)。MECCANO 与 IndustReal 等资产须从官方渠道取得并记录来源、许可、版本、split、哈希；运行时不能用动作真值替代观察证据。开发污染序列不可进入正式 test。

后续需验证步骤接续准确率、unknown/拒绝推进边界、跳步与逆序、端到端延迟、失败恢复与跨任务配置成本。本次只验证轻量软件行为，未启动训练、真实数据实验或付费服务。

本次未新指定项目许可证；第三方依赖与数据遵循各自许可。
