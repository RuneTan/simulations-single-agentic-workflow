# HEPToolBench pilot

[中文](README.zh-CN.md) · [Experiments](../README.md)

Three pinned upstream card-writing requests were each run with and without gates using the same model and budgets within each pair. The workflow executed the requests through its ordinary task API. It still used the generation-and-analysis route, so all six trials missed the standalone card delivery requested by the benchmark. This experiment primarily identifies a task-routing mismatch.

| Result | Gated | Ungated |
|---|---:|---:|
| Workflow completed | 2/3 | 3/3 |
| Requested standalone card delivered | 0/3 | 0/3 |
| Total tokens | 654,104 | 483,157 |

Four internal process cards scored 0.30. For task17, the generated run banner matched all six requested settings and scored 1.00, while the later working card scored 0.85 after the seed was reset. Task16 failed on the then-uncovered alphaScheme Python compatibility path; the compatibility layer has since been extended, while these historical results remain unchanged.

[Trial table](results/trials.csv) · [Detailed scores](results/summary.json) · [Source/version hashes](provenance.json) · [Requests](requests.json)

To repeat the three pairs with new tasks (uses your model API):

```bash
./.venv/bin/python -I -B experiments/heptoolbench/run_trials.py experiments/heptoolbench/runs/new-campaign
./.venv/bin/python -I -B experiments/heptoolbench/score_trials.py experiments/heptoolbench/runs/new-campaign/campaign.json experiments/heptoolbench/runs/new-campaign/scores
```

The scorer checks the pinned upstream file hashes and reference controls before scoring. Upstream cases and scorers retain their [license](upstream/LICENSE). New runtime data is ignored by Git.
