# Experiments

[中文](README.zh-CN.md) · [Workflow review](../docs/REVIEW.md)

| Experiment | Question | Published material |
|---|---|---|
| [Gate ablation](gate_ablation/README.md) | How did automatic checks affect four paired generation tasks? | Costs, input/output tables, histogram data and five figures |
| [HEPToolBench](heptoolbench/README.md) | Does the generation workflow deliver the cards requested by three external benchmark cases? | Verbatim cases/scorers, API runner, score adapter and six-trial results |
| [UFO compatibility](ufo_compatibility/README.md) | Can all ten official variants generate a small sample under Python 3? | Per-model event counts and source-integrity checks |
| [Portability](portability/README.md) | Can a fresh checkout install and run independently? | Release validation summary |

The first three entries summarize historical pilot runs. Tables preserve the measured numbers; path labels in public JSON are relative to the originating task or project. Original raw events and trajectories stay outside Git. Historical hashes identify those local source records and are not checksums of rewritten public summaries.

For a new campaign, create a distinct directory under `experiments/` for requests, manifests, result tables and figures. The workflow keeps live task state under `workspace/tasks/`; campaign manifests reference it. Add only reviewed publication files to Git. `.gitignore` includes the published campaigns explicitly and ignores new runtime directories by default.
