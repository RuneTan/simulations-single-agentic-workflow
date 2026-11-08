# HEPToolBench 试验

[English](README.md) · [实验](../README.zh-CN.md)

三个固定版本的上游配卡请求各运行一次带门控和无门控任务，每对使用相同模型与预算。通过工作流正常任务 API 执行，但当前仍走生成与分析流程，因此六轮都没有交付 benchmark 要求的独立卡片。此实验主要定位了任务分流与交付形式的适配问题。

| 结果 | 带门控 | 无门控 |
|---|---:|---:|
| 工作流完成 | 2/3 | 3/3 |
| 交付要求的独立卡片 | 0/3 | 0/3 |
| 总 token | 654,104 | 483,157 |

四张内部过程卡得分为 0.30。task17 的实际生成 banner 符合六项设置，得分 1.00；后续工作卡因种子重置得分 0.85。task16 因当时尚未覆盖的 alphaScheme Python 兼容路径失败；兼容层现已扩展，此处保留当时的原始结果。

[逐轮表格](results/trials.csv) · [详细评分](results/summary.json) · [来源与版本哈希](provenance.json) · [请求](requests.json)

使用新任务重复三组配对（调用自己的模型 API）：

```bash
./.venv/bin/python -I -B experiments/heptoolbench/run_trials.py experiments/heptoolbench/runs/new-campaign
./.venv/bin/python -I -B experiments/heptoolbench/score_trials.py experiments/heptoolbench/runs/new-campaign/campaign.json experiments/heptoolbench/runs/new-campaign/scores
```

评分器先核对上游文件哈希和参考卡片，再进行评分。上游用例与评分器保留其[许可证](upstream/LICENSE)。新运行数据由 Git 忽略。
