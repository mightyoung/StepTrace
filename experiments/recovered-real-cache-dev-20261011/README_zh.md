# 本机真实缓存恢复与最小进度证据对照

接续 `08eed4dd68fee2dd6091ba054e653fd6479c4420`。本次实际恢复查找进入旧 display-project 本机目录，读取其 AGENTS、研究简报、审计清单与源文件。原私有素材保持原位置，不上传；公开仅新代码、去敏派生结果与检查记录。

## 知识包旧测试修复

test_vlm_state_risk_contract.py 的知识包集合补入已有 gv06-reference-port.yaml；仍要求精确的三包集合、全体可加载、非正式状态及匿名task_id。第三包既有 DEVELOPMENT_ONLY 状态及所有知识包字节不变，不删除文件、不放宽研究门禁或改研究事实。

原同组62项检查全部通过，前轮61通过/1失败的记录保留不重写；另10项声明检查、7项消费边界检查与本轮5项真实缓存支持检查通过，共84项有界软件检查。没有宣称整个仓库全套通过。

## 恢复查找的实际结果

- 在本机 Documents、development、Downloads 与临时缓存中查找项目/素材/实验记录，找到 Downloads 内旧 display-project，而非假定云目录存在。
- 临时安装状态方法审阅包有三份文字文件、图像数0，只是AI方法意见；不作为视觉结果。
- Library 定向查询并读到两份旧 StepTrace 报告：三条件F/P/PF报告与七份有限证据报告，后者给原runs/packets目录名，前者给三份首答SHA。这些是报告与叙述摘录，不是完整首答JSON，不能补出原结构字段。未下载或公开原文附件。
- 完整公开研究树有 q31/q68/q92 三份原JSON，但无旧 IndEgo 分离原输入/首答。在本机旧项目中也未命中119–128秒探针、IKEA1743/1746/1752帧或旧runs原包；有界查找未找到，不证明所有外部存储都不存在。
- 恢复 G-V0-6 的真实12秒 IndustReal 切片、4张帧和4份原始Qwen输出。切片SHA匹配旧clip-info；四帧字节数/SHA全匹配清单。文件虽叫 ground-truth-manifest，其来源含旧AI复核和未核作者身份的审阅记录，不能认证为独立人工真值；完整原视频仍缺，未进行新推断。
- 恢复真实 MECCANO-0009 351帧渲染视频及同帧号的既有严格检测日志。视频SHA与旧AGENTS登记值一致，首帧、对照两帧与中帧均可解码；日志351帧连续编号，annotation_used全部false。0009保持development曝光与正式test tombstone，不能恢复为独立测试集。

## 一个新的真实缓存最小对照

确定性选择既有日志中第一个“本帧确认→下一帧unknown但保留同一步骤”的相邻对：第5/6帧。

| 帧 | cached decision | current_step | confirmed_step | 新确认资格（仅缓存记录） |
|---|---|---|---|---|
| 5 | normal | pick_components | pick_components | 有 |
| 6 | unknown | pick_components | null | 无；保留历史进度 |

两行是真实视频上已保存的检测输出，不是为试验编造的历史夹具，原日志未改。单读 current_step 会发生预期 RED 投影碰撞；保留 decision/confirmed_step/历史作用域可以区分“保留旧进度”与“这一帧新确认”。现有current_step本来表示进度，不承诺本帧新观察，未证明生产bug。第6帧的unknown针对下一预期 align_components 的证据不足，不等于 pick_components 物理状态变未知，更不等于发生拆卸或遮挡；本例不能替代旧真实分离对照。

全日志仅有2行normal确认、349行unknown，344行unknown同时保留历史进度。它们是缓存字段计数，不是识别正确率、物理弃答率或方法提升。结果同时保存整日志SHA及两条原记录的canonical SHA；视频只记录哈希/规格/解码检查，不公开像素。这是事后开发选例，取后续行用于选择，不冒充事前登记或线上未来信息可用性。

## 复现与未测

```sh
# 真实素材仍在本机私有位置。传入已恢复的原日志与渲染视频；不下载素材。
python3 -B check_progress_support.py --events "$EVENTS_PATH" --video "$VIDEO_PATH" --output results.json
python3 -B -m unittest -v test_progress_support.py

# 仓库根目录
python3 -B -m pytest -q tests/test_vlm_state_risk_contract.py tests/test_vlm_state_memory.py tests/test_state_machine.py tests/test_state_risk_metrics.py tests/test_g1_derivation.py
```

代码仅解析已有机器输出和使用环境现有OpenCV解码，没有模型调用、训练、依赖安装或新媒体获取。软件负例（unknown仍伪填confirmed_step、annotation_used=true）明确合成；不计新的真实样本。物理关系保持、误撤销率和识别准确率仍null，独立人工真值核验数0。旧G-V0-6切片的AI语义记录不升级为人工GT。

下一步唯一需要定位的材料是旧 IndEgo 真实拆卸原实验包：119–128秒，相机支架底座—长立柱，含原帧清单与H/F/HF原JSON。一个可访问本机路径或Library文件名即可继续恢复原记录；不要求新增拍摄、标注或模型答复。取得前继续保留真实缓存与失败边界，不从摘要补造分离夹具。
