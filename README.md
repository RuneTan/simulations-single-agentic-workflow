# Simulations Single Agentic Workflow

[中文](README.zh-CN.md) · [Architecture and results](docs/REVIEW.md) · [Parameters](docs/ParaList.md) · [Checkers](docs/ToolList.md) · [Linux / Nikhef setup](docs/DEPLOYMENT.md) · [Experiments](experiments/README.md)

A single Agent turns a natural-language particle-physics request into a MadGraph simulation and an analysis. LangGraph manages the stages and saved state. Five independent scripts check the environment, request, native cards, event sample and experience entry at the corresponding handoffs.

The Agent uses general file and shell tools to select a model, configure cards, generate events, plot results and repair errors. Each task retains its inputs, checker feedback, visible execution trajectory, token usage and outputs.

## Quick start

Linux x86_64, Bash, curl, tar, make, g++ and gfortran are required. The installer creates local Python environments and downloads checksum-pinned model/backend sources; no administrator access is needed for these project dependencies.

```bash
git clone https://github.com/RuneTan/simulations-single-agentic-workflow.git
cd simulations-single-agentic-workflow
./scripts/setup.sh
cp .env.example .env.local
chmod 600 .env.local
# Edit .env.local to supply your model API key and model name.
./run.sh chat
```

The current model client targets DeepSeek's chat-completions API; configure `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` through the environment or `.env.local`. The example contains no credential. New model calls incur provider usage.

Enter a request in English or Chinese. `/tasks` lists saved tasks, `/resume task1` resumes one, and `/quit` exits. During planning the Agent can ask a question in the same window. For a fully specified starting request, see [examples/request.txt](examples/request.txt).

```bash
./run.sh inspect
./run.sh test -q
./.venv/bin/python -I -B scripts/run_task.py --request-file examples/request.txt
```

## Inputs and outputs

Generation settings describe the process, SMEFTsim model, parameter point, coupling orders, beams, PDFs, cuts, event count and seed. Analysis settings describe observables, particle pairing, selections, binning and normalization. A process-only request starts at the SM point; the Agent records or asks about additional simulation settings.

Tasks are automatically numbered under `workspace/tasks/taskN/`. `outputs.json` indexes the LHE sample, plot, numerical table, analysis program and summary. `trajectory.jsonl` and `trajectory_summary.json` record the visible decisions, tool calls, checks, repairs, timing and API token usage. Resuming appends to the same task.

The current generation route produces a sample and analysis. Dedicated card-only delivery is a future extension. Checkers cover explicit configuration and numerical consistency; physics interpretation remains part of Agent and user review. General shell commands execute with the operating-system permissions of the account running the workflow.

## Repository map

| Location | Purpose |
|---|---|
| `src/nsbi_agent/workflow/` | LangGraph stages, model client, tools, jobs, state and trajectories |
| `src/nsbi_agent/tools/` | Five independent checkers |
| `src/nsbi_agent/interaction/` | Task allocation and terminal questions |
| `scripts/` | Installation, backend launchers and reusable verification |
| `tests/` | Modular regressions and small independent fixtures |
| `examples/` | Natural-language request and checker input templates |
| `docs/` | Bilingual architecture, parameter and deployment guides |
| `experiments/` | Public result data, plots and benchmark adapters |
| `deploy/nikhef/` | HTCondor submission example |

Installation creates `.venv/`, `.tools/`, `models/` and `backends/`. Execution creates `workspace/` and `memory/`. These directories, local credentials and raw task traces are excluded from Git.

## Reproducibility

Python dependencies are locked in `uv.lock`. Agent Python is 3.12.12; MadGraph 2.9.27 uses a separate Python 3.10.12. Ten official SMEFTsim 3.0.2 variants are installed, plus the pinned reference collection that preserves existing process-only defaults. A scoped import hook converts legacy UFO support syntax in memory while preserving model source hashes.

The [experiment index](experiments/README.md) separates historical pilot results from current portability checks. [Progress](docs/PROGRESS.md) records the latest release changes.

No project license is specified. Third-party notices apply to the named upstream material and dependencies; see [attribution](docs/THIRD_PARTY_NOTICES.md).
