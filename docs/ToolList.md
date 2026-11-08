# Independent checkers

[中文](ToolList.zh-CN.md) · [README](../README.md) · [Architecture](REVIEW.md) · [Parameters](ParaList.md)

The workflow calls these five modules automatically. Their CLI forms also support independent checks of existing files.

| Number / command | Function | Input | Output |
|---|---|---|---|
| 1 inspect | Inventory environment and compare expectations | Optional package/backend names and expected versions | Actual versions, paths, matches and differences |
| 2 validate-spec | Resolve SM/EFT points and check parameter consistency | simulation; optional production/analysis/extensions | Original request, hash, defaults/provenance, points and differences |
| 3 validate-cards | Compare actual cards with intent | request, absolute card paths; optional banner/hash | Parameters, contribution orders, scales, cuts and recorded configuration |
| 4 check-sample | Stream initialized LHE/LHE.gz event records | event_file; optional expected count, weight IDs, exit code/log | Event count, nominal/reweight sumw/sumw2, negative counts and log tail |
| 5 check-experience | Assemble experience and check evidence/review | lesson, applies_to, evidence, review | ready/review/revise, evidence-bound entry and entry hash |

Tool 3 compares cards before generation (`configuration`) and the recorded banner after generation (`run_record`). The latter reads MG5 2.9.27's CDATA wrappers and list-valued settings. Tool 4 reports stored event weights separately from init-reported cross sections. Tool 5 binds a lesson to the evidence version and review actually supplied.

The scope is environment identity, independent SMEFTsim inputs, MG5 2.9.27 configuration, LHE structure/statistics, and experience evidence. The Agent and user assess the physical interpretation and task fit.

## Run the small examples

Artifact readers use absolute paths. This command fills the example templates with paths from your checkout and creates the two-event sample report:

```bash
./.venv/bin/python -I -B scripts/prepare_examples.py
./run.sh validate-spec examples/request_sm.json
./run.sh validate-cards .cache/examples/check_existing_cards.json
./run.sh check-sample .cache/examples/check_sample.json
./run.sh check-experience .cache/examples/check_experience.json
```

The JSON files in `examples/` use `{PROJECT_ROOT}` and `{EXAMPLE_OUTPUT}` as template tokens. `prepare_examples.py` writes runnable inputs to `.cache/examples/`; the checker APIs themselves receive explicit file paths. Full contracts, units and defaults are in the parameter list.
