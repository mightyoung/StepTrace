# StepTrace 接续：实际恢复素材与真实缓存对照

接续 `08eed4dd68fee2dd6091ba054e653fd6479c4420`。

[本轮过程、代码与结果](../../../experiments/recovered-real-cache-dev-20261011/README_zh.md)。修复知识包清单遗漏第三包的测试，知识包/生产语义不改；原相关62项全部通过，另22项有界缓存检查通过。前轮失败不覆盖。

本机实际找到旧 display-project，恢复真实MECCANO-0009视频/351行检测缓存，以及G-V0-6真实切片/4帧/4份Qwen原输出。匹配登记哈希并解码，私有原素材不公开。后者的ground-truth命名不认证独立人工GT。

新的真实缓存第5/6帧对照：current_step相同，但前者本帧confirmed_step有值，后者unknown且无新确认。保留历史与当前证明资格即可区分，投影碰撞不构成生产bug或方法增益。0009仍development且正式test tombstone，未计算准确率/误撤销率。

旧IndEgo119–128秒真实分离原包仍未找到；Library只恢复相关报告摘录，没有完整JSON。唯一待用户定位：含原帧manifest与H/F/HF原JSON的那个原包可访问路径/文件名。不补历史夹具，不新增模型/训练/安装/付费或媒体下载。
