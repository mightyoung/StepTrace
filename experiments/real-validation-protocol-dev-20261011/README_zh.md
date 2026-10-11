# StepTrace 最小真实验证接入

本目录延续 [冻结协议](PROTOCOL_zh.md)，补上未来真实评估材料的本地离线验收与描述性计分入口。实现依据：公开缓存可用于字段回放，但此前没有入口能同时核验媒体、逐帧前缀、盲标参考和三路真实输出是否齐全。

**真实执行门仍关闭。** 当前没有可接入的授权视频、完整帧清单、合格盲标真值或三路真实输出；官方新材料的访问仍为 403。这里没有调用模型、训练、安装依赖或生成真实效果结论。

## 交付

- [输入包格式与验收条件](BUNDLE_FORMAT_zh.md)：冻结视频/帧/裁切/标注/输出字段以及阻断条件。
- [离线验收与计分器](validate_bundle.py)：缺任一媒体、标签、挑战类别或任一臂机会记录时停止，不输出分数；完整包只输出挑战集 FCR、coverage 和 unknown 计数，不作推断。
- [q31/q68/q92 缓存字段对照](cache-comparison.html)：可分享的独立 HTML，仅呈现已核验缓存中的字段，不包含或伪造原图。
- [缓存对照生成器](render_cache_comparison.py)：从旧离线回放和来源 manifest 校验哈希后生成 HTML。

## 复现

在仓库根目录：

```sh
python -B -m unittest -v tests.test_steptrace_real_validation_bundle
python -B experiments/real-validation-protocol-dev-20261011/render_cache_comparison.py
```

真实材料到齐后，按 [输入格式](BUNDLE_FORMAT_zh.md) 生成 bundle，再运行：

```sh
python -B experiments/real-validation-protocol-dev-20261011/validate_bundle.py \
  --bundle /path/to/evaluation-bundle.json \
  --root /path/to/evaluation-assets \
  --output /path/to/descriptive-score.json
```

CLI 返回 2 并打印 `BLOCKED` 时，不能把不完整输入解释为模型拒判或指标结果。只有输入齐全、验证通过后才写出 `DEVELOPMENT_DESCRIPTIVE_ONLY` 产物。

## 当前边界

公开 q31/q68/q92 是同一开发事件的相关前缀和 AI 缓存标签。q68 的当前直接关系记录为 unknown，沿用 f1743 连接只属条件历史；步骤完成仍 unknown。三行不是三个独立物理样本，也不是人工真值。缓存来源 SHA 与文件 SHA 列于对照 HTML 和 [MANIFEST.json](MANIFEST.json)。

合成数据只存在于单元测试的临时目录，用于校验拒绝条件；不保存为研究样本、不计分到真实材料，也不替代遮挡保持、真实分离和身份变化三类真实事件。
