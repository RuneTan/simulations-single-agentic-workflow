# Workflow and experimental review

[中文](REVIEW.zh-CN.md) · [Entry point](../README.md) · [Tools](ToolList.md) · [Parameters](ParaList.md) · [Progress](PROGRESS.md) · [Experiments](../experiments/README.md)

## Workflow

One Agent interprets requests, prepares cards, runs MadGraph, analyzes results and explains the outputs. LangGraph manages task state, background jobs and handoffs. Five checkers cover the environment, request, native cards and banner, samples, and experience admission. The tool and parameter pages define the individual checks.

```mermaid
flowchart LR
    A[Request and workspace] --> B[Environment and request checks]
    B --> C[Cards and review]
    C --> D[Launch and wait]
    D --> E[Sample and banner checks]
    E --> F[Analysis and delivery]
    F --> G[Experience admission when needed]
```

Passing checks advance the task. Repairable issues return to planning or preparation for another check. For `needs_review`, the Agent binds its disposition to the specific checker, version, phase and fingerprint. `accepted` advances, `repaired` rechecks, and `deferred` records a question and waits for the user. Two automated repairs are allowed by default. The current generation path delivers events, a plot, a table, an analysis program and a summary.

## Execution and review

1. Use `./run.sh chat` for ordinary tasks. Batch runs call `create_task`, `run_task` and `run_resume`. New tasks receive separate workspaces and record generation and analysis settings separately.
2. Exit 3 waits for an existing job. Resuming queries the same `job_id`. Exit 5 waits for `user_decisions`. State, checkpoints and logs stay in the task workspace.
3. For new comparisons, freeze the code, requests, model and paired budgets. `runtime.gates_enabled` defaults to `true`. Setting it to `false` removes automated checks and their feedback, review and repair path. The project Agent runs and repairs tasks. The external evaluator reads the artifacts.
4. Start with `outputs.json`, `summary.json` and `trajectory_summary.json`. Inspect native cards, banners, LHE, histograms and `trajectory.jsonl` as needed. Report execution, request fidelity, numerical agreement and explanation separately. API input tokens include cache hits. Record reference-computation costs separately from original task costs.
5. Retrieve shared experience first and persist candidates explicitly after checker 5. Parallel campaigns allocate independent workspaces by process, parameter point or seed. Final analysis waits for its sample.

```bash
./run.sh test -q
./.venv/bin/python -I -B scripts/check_deployment.py
```

## Gate ablation

| Metric | Gated task1–4 | Ungated task5–8 |
|---|---:|---:|
| Execution completed | 4/4 | 2/4 |
| Requested generation settings and numerical agreement | 4/4 | 0/4 |
| Total tokens | 639,321 | 525,994 |

The four pairs are SM dileptons, `clq1` dileptons, `cHW` \(Zh\) and `cuGRe` top pairs. Each target was 20,000 events. task5 exhausted its preparation budget, task6 failed in generation, and task7/8 each produced 20,000 pure dimension-six-squared events in place of the requested combined prediction. Replaying checker 3 with the actual banners returned `needs_review`, leaving that distinction for Agent review. task1 passed the card and sample checks and contained an erroneous radiative-tail explanation.

Each process was paired once, using the historical task-specific budgets. A separately authored LO reference with an independent seed supplies 100,000 events per process. Costs, inputs and outputs, five figures, recomputed data are indexed with the experiment; original trajectories remain local. [Gate ablation evidence](../experiments/gate_ablation/README.md)

## External benchmark and backend verification

Three HEPToolBench MadGraph card requests were each run with and without gates. All six lacked the requested standalone card. Backend completion was 2/3 gated and 3/3 ungated. task17's banner matched all six requested settings and scored 1.00. The current generation path routed card requests through simulation and analysis. task16 failed to import `U35_alphaScheme`. A separate Python 3 compatibility extension subsequently ran 100 SM dilepton events for each of the ten official UFO variants. [HEPToolBench report](../experiments/heptoolbench/README.md) · [UFO runtime verification](../experiments/ufo_compatibility/summary.json)
