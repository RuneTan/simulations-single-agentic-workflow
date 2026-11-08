# 实验

[English](README.md) · [工作流评述](../docs/REVIEW.zh-CN.md)

| 实验 | 问题 | 公开材料 |
|---|---|---|
| [门控消融](gate_ablation/README.zh-CN.md) | 自动检查如何影响四组配对的生成任务？ | 成本、输入输出表、直方图数据和五张图 |
| [HEPToolBench](heptoolbench/README.zh-CN.md) | 生成工作流能否交付三个外部 benchmark 用例要求的卡片？ | 原始用例与评分器、API 执行和评分程序、六轮结果 |
| [UFO 兼容性](ufo_compatibility/README.zh-CN.md) | 十个官方变体能否在 Python 3 下生成小样本？ | 各模型事件数与源文件完整性检查 |
| [可迁移性](portability/README.zh-CN.md) | 全新下载能否独立安装和运行？ | 发布验证摘要 |

前三项总结历史试验。表格保留测得数值；公开 JSON 中的路径相对于原任务或项目。原始事件和轨迹不进入 Git。历史哈希标识本地原始记录，不作为改写后的公开摘要校验和。

后续实验在 `experiments/` 下单独建立目录，存放请求、清单、结果表与图。工作流的活动状态仍由 `workspace/tasks/` 管理，实验清单引用它。仅将检查过的公开文件加入 Git；`.gitignore` 明确列出已发布实验，默认忽略新运行目录。
