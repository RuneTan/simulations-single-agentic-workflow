# Parameter list

[中文](ParaList.zh-CN.md) · [README](../README.md) · [Tools](ToolList.md) · [Review](REVIEW.md) · [Progress](PROGRESS.md)

Fields are grouped by task. The Agent chooses models and operations; tools check the structures and values below.

## 1 Environment

| Field | Shape / meaning |
|---|---|
| packages/backends | name→expected version; backends: madgraph/gfortran/make |
| package_names/backend_names | Inventory actual versions and locations |

## 2 Request parameters

Input: `{simulation, production?, analysis?, ...}`. A process-only request uses SM. Omitted model identity uses tutorial U35/Mw; explicit identity defaults to official, with collection selectable.

| Field | Shape, units and meaning |
|---|---|
| simulation.process | Flat string or initial_state/final_state/multiparticles/kind object; explicit aliases |
| simulation.model | collection, model_id, version, basis, flavour_scheme, ew_input_scheme, restriction |
| Model premises | Warsaw; general/U35/MFV/top/topU3l; MwScheme/alphaScheme; version 3.0.2 |
| model.restriction | Actual additional card name or full; chosen by the Agent and compared in card checks |
| simulation.sm_inputs | Exact independent SM name→finite value; masses/Yukawas/widths GeV, Gf GeV^-2, others dimensionless |
| eft.cutoff_GeV | Positive Λ, default model value; separate from μR/μF |
| eft.coefficients | Wilson name→dimensionless value; independent zero_then_set per point |
| eft.contribution | sm/interference/dim6_squared/propagator_interference |
| eft.prediction | sm_plus_linear/sm_plus_linear_and_dim6_squared; choose this or contribution |
| eft.propagator_corrections | excluded/linearized for combined predictions |
| eft.coefficient_policy | zero_then_set |
| eft.varying_coefficients | Independently varied Wilson names |
| eft.width_policy/sm_higgs_loop_vertices | fixed_ufo_defaults/excluded |
| generation.nevents/seed | Positive baseline event count/random seed |
| generation.beam_energies_GeV/beam_types | Two positive energies; two MG lpp integers, 0 particle beam and 1 proton beam |
| generation.pdf | {label,set_id}; nonempty label and nonnegative integer set_id; null allowed for decay |
| generation.cuts | List of {observable,minimum,maximum,unit}; [] explicitly selects no cuts |
| cut.observable | charged_lepton_pt, abs_charged_lepton_eta, abs_charged_lepton_rapidity, dilepton_mass |
| Cut bounds/units | Nonnegative, possibly one-sided; pt/mass in GeV, eta/y dimensionless |
| simulation.reweighting | List of {weight_id,contribution?,coefficients?}; each point starts from zero |
| simulation.stage | Task-stage label, default parton; preserved in the original request |
| production.qcd_order/qed_order | Nonnegative amplitude coupling-power caps |
| production.renormalization_scale_GeV/factorization_scale_GeV | Positive fixed μR/μF; μF may be null |
| production.maxjetflavor/bwcutoff | Nonnegative jet-flavour cap/positive resonance-window width multiple |

Complete names, coordinates, defaults and units: [model inventory exporter](../scripts/export_model_inventory.py). Wilson external counts: general 2499, U35 85, MFV 130, top 275, topU3l 182; each has two EW schemes.

Export after installation: `./.venv/bin/python -I -B scripts/export_model_inventory.py .cache/model_inventory.json`.

All ten official UFO variants passed a Drell–Yan smoke run with MadGraph 2.9.27 and Python 3.10. Each run generated 100 events at the SM point. [Verification results](../experiments/ufo_compatibility/summary.json)

MwScheme uses independent MW/Gf/aS; alphaScheme uses aEW/Gf/aS, with aEW equal to alpha. The checker tests the real EW domain. Additional restrictions fix/tie inputs and are compared with actual cards. full retains the UFO's flavour/EW premises.

## 3 Actual cards

| Field | Shape / meaning |
|---|---|
| request | Full original tool-2 request |
| artifacts.param_card/run_card/process_card | Actual absolute paths; freely chosen task directories and filenames |
| artifacts.reweight_card/banner | Optional files; Agent reviews reweight execution, banner compares recorded configuration |
| expected_intent_sha256 | Optional original-request hash from tool 2 |
| phase | configuration or run_record, which compares a banner |
| backend_version | Check-rule version, default 2.9.27 |

EFT orders use ordered-pair comparison; fixed μF uses effective per-beam flags. Eta checks use actual lepton masses, etal implements y, and the generic lepton-cut 20-GeV mass threshold is checked.

## 4 Run artifacts / sample

| Field | Shape / meaning |
|---|---|
| event_file | Absolute LHE/LHE.gz path |
| expected_events | Optional positive expected count |
| required_weight_ids | Weight IDs required for the task |
| run_exit_code | Optional integer, compared with zero |
| log_file | Optional absolute path; includes the last twelve lines |

nominal is XWGTUP. weights follows wgt IDs or compact weights paired with initrwgt IDs. count/sumw/sumw2 are entries, weight sum and sum of squares; negative/zero count signs. Declared, observed and required IDs are checked for event coverage. Cross sections are separate init-reported values in pb; weight statistics follow stored conventions. events counts formal event records and event_groups counts groups; init includes pdf_group_ids/pdf_set_ids. Statistics and the raw-file hash share one read.

## 5 Experience admission

| Field | Shape / meaning |
|---|---|
| lesson | One explicit reusable lesson |
| applies_to | Nonempty name→text map, such as backend/version/model/task |
| evidence[].path | Absolute evidence path |
| evidence[].sha256 | Optional expected hash to detect changes |
| evidence[].role | observation cites original records; validation parses unique-key, finite-number JSON and prioritizes status, then Boolean passed when status is absent |
| review.by/verdict/reason | Reviewer, accept/revise and reason; completed by the user or Agent |

Outputs: admission=ready/review/revise, entry and entry_sha256. entry binds the evidence version actually read and its review record; validation conclusion and hash share one snapshot for Agent-managed memory/skills.

## 6 Task workflow inputs and evidence

For daily use, enter natural language through `./run.sh chat`. It creates `workspace/tasks/taskN/taskN.json` above the highest existing number, automatically filling task_id, workspace and user_request. The manual entrypoints below remain available.

Planning's ask_user accepts `{question, choices?}`: one question and optional choices. The window saves answers in user_decisions; resume supplies them as clarifications to planning in the same task, preserving the original request separately. Deferred review questions can also be answered in the window. Final plots, tables and event files stay under that taskN; outputs.json indexes their actual locations through plot, table, events, summary and program.

`./run.sh task /absolute/path/task.json` starts one task; `./run.sh resume /absolute/path/task.json` continues the saved task. `workspace` must be an absolute path under the project `workspace/` directory. Only task_id, user_request and workspace are required; omitted runtime fields use the defaults below.

| task.json field | Shape / meaning |
|---|---|
| task_id | Stable task name; the interaction layer assigns task1, task2, etc.; manual inputs may set it explicitly |
| task_type | Optional task-stage label, default generation |
| user_request | Complete natural-language request; the Agent derives the structured physics/analysis request from it |
| workspace | Absolute task directory under project workspace/; the interaction layer fills workspace/tasks/taskN |
| runtime.gates_enabled | Boolean, default true; false selects ablation: skip five automatic checkers and gate repair/review, retaining general tools, jobs and trajectories |
| runtime.max_repairs | Automatic repair-count cap; positive integer or null for no count cap; default 2 |
| runtime.cores | Allocated core count, positive integer; default 2 |
| runtime.job_timeout_seconds | Background job timeout, positive integer; default 3600 |
| user_decisions | Optional topic → answer-text map; the window saves planning/review answers automatically, or they can be entered manually before resume |
| fixtures[] | Optional fault injections; run_card_beam_mismatch with field, from, to rewrites that field in the live run_card once, before check 3 |
| experience.draft | Whether to draft and admit a reusable lesson after delivery |
| experience.scope | applies_to scope for the drafted lesson |
| analysis | Separate from generation; the pair-mass template reads observable, pair_pdg_ids, bins and range_GeV; the template default is 10 bins. |

Task workspace layout:

```text
workspace/tasks/<task_id>/
  taskN.json, task.snapshot.json # interactive entry; manual tasks may use task.json
  outputs.json                  # final artifact locations
  request.txt, request.json, defaults.json
  state.json                      # Current durable state snapshot
  trajectory.jsonl                # schema_version=1; append-only visible execution trace
  trajectory_summary.json         # calls, tokens, stages, repairs, interventions and jobs
  events.jsonl                    # Append-only task trace (nodes, checks, tools, jobs)
  checkpoints.sqlite              # Append-only per-stage state history
  model_trace/                    # calls.jsonl plus per-stage message records
  checks/                         # Original checker results by number and version
  checks/cards_vNN/               # Frozen copies of the checked cards
  attempts/NNN/                   # cards.mg5, native process, cards, run_events.sh
  jobs/<job_id>/                  # job.json, run.sh, job.log, exit_code, finished_epoch
  logs/commands/                  # Shell command records
  analysis/                       # analysis.py, analysis_config.json, analysis.json, plot.png, summary.md
  fixtures/NNN/                   # Injected fault records: before, after, diff
  summary.json                    # Final task summary
memory/candidates/<sha256>.json   # Draft lessons, never retrieved
memory/entries/<sha256>.json      # Admitted entries, retrieved by later tasks
```

Agent finish_stage contract:

| Field | Meaning |
|---|---|
| stage | Stage being completed; must match the running stage |
| complete | Must be true; a false completion fails the stage |
| summary | Short plain-text readout retained as the Agent explanation |
| artifacts | Stage outputs as absolute paths inside the task workspace: request/defaults; param_card/run_card/process_card; plot/table/program/summary; candidate |
| proposed_job | For prepare_cards: script (absolute run_events.sh), cwd (process directory) and expected_outputs |
| dispositions | For needs_review: one {topic, disposition accepted/repaired/deferred, reason, evidence} per review topic |

Every recorded disposition is bound to `check_number`, `check_version`, `check_phase`, `check_sha256` and the covered `topics`; only the same check record can be reused. Accepted advances; repaired returns to the owning stage (check 2 → planning, checks 3/4 → card preparation), consumes one repair and is rechecked; deferred pauses the task with exit code 5 and resumes after `task.user_decisions` supplies every pending topic. A failed run-record check always repairs or fails. Job records include `started_epoch` and `finished_epoch`: generator time is the difference between them, and checker time sums the full check history. Experience records `experience.stage_usage`: one entry per operational stage (prepare_cards, dispositions, analyze_deliver) with the entry hash, whether it was applied and the stage summary; applied requires the summary to cite the entry id.

An invalid finish_stage or a missing review-topic disposition fails the stage; the runner leaves the workspace and one concrete next step. Task exit codes: 0 completed, 1 failed, 2 invalid task input, 3 waiting for a background job (run resume later), 5 awaiting a user decision (a deferred disposition: the task pauses with the pending questions and resume continues once `task.user_decisions` covers every pending topic).


## Analysis and execution arrangements

analysis separately preserves samples/objects, observables, units, pairing, selections, weight IDs, normalization, bins, sumw/sumw2, correlations, axes and export formats. The Agent handles plotting and debugging. Generation cuts define sampled phase space; analysis selections act on existing events.

Task execution records CPU allocation and job timeouts and manages directories, logs, retries and caches. The host or batch allocation supplies memory limits. Model/card hashes record provenance. Official local models live under models/smeftsim/official/3.0.2/.

Every task collects trajectory routinely with no input switch; pause/exit refreshes statistics and resume appends. Records cover visible model messages, tool arguments/results, checker feedback and branches; images use hash references and commands retain log paths. Statistics distinguish tool errors, automatic repairs, user answers and external operator interventions. attempts counts completed card-stage submissions; incomplete stages remain grouped by stage_run. analysis.pair_pdg_ids defaults to [11,-11] and also accepts [23,25] or [6,-6]; the template selects one final-state particle for each of two distinct IDs and computes pair invariant mass.

## Agent budgets

| Stage | Default model-call limit | Default output tokens per call |
|---|---|---|
| plan_resolve | 10 | 1800 |
| prepare_cards | 16 | 2200 |
| dispositions | 6 | 1400 |
| analyze_deliver | 14 | 3000 |
| experience | 6 | 1600 |

runtime.agent_budget_multiplier defaults to 1; a positive integer scales both columns, while null removes the stage-call cap and omits max_tokens from output requests. runtime.max_repairs defaults to 2; null removes its count cap. When max_tokens is omitted, the provider applies its model-specific response and context limits. Total tokens have no separate cap and sum API-reported input/output across calls; cache hits remain part of input tokens. stage_budget in trajectory records effective limits; see REVIEW for measured usage.

See the [Gate ablation comparison](../experiments/gate_ablation/README.md) for paired inputs/outputs, usage and contribution checks; failed runs retain their artifacts.
