# 参数表

[English](ParaList.md) · [README](../README.zh-CN.md) · [工具清单](ToolList.zh-CN.md) · [检查报告](REVIEW.zh-CN.md) · [进度](PROGRESS.zh-CN.md)

字段按任务分类；模型选择与操作方式由 Agent 决定，工具核对下列结构和数值。

## 1 环境

| 字段 | 格式／含义 |
|---|---|
| packages／backends | 名称→期望版本；后端可选 madgraph、gfortran、make |
| package_names／backend_names | 查询实际版本与位置的名单 |

## 2 请求参数

输入 `{simulation, production?, analysis?, ...}`。只给过程时采用 SM 点；模型默认教程 U35/Mw 快照，显式模型身份默认 official，collection 可明确选择。

| 字段 | 格式、单位与含义 |
|---|---|
| simulation.process | 平坦过程字符串，或 initial_state／final_state／multiparticles／kind 对象；别名显式给出 |
| simulation.model | collection、model_id、version、basis、flavour_scheme、ew_input_scheme、restriction |
| model 前提 | Warsaw；general/U35/MFV/top/topU3l；MwScheme/alphaScheme；版本 3.0.2 |
| model.restriction | 实际附加卡名或 full；默认由 Agent 选择，卡片检查对照该选择 |
| simulation.sm_inputs | 精确 SM 独立输入名→有限数值；质量／Yukawa／宽度为 GeV，Gf 为 GeV^-2，其余无量纲 |
| eft.cutoff_GeV | 正数 Λ，默认模型值；与 μR、μF 分别指定 |
| eft.coefficients | Wilson 名→无量纲数值；各点独立 zero_then_set |
| eft.contribution | sm／interference／dim6_squared／propagator_interference |
| eft.prediction | sm_plus_linear／sm_plus_linear_and_dim6_squared，与 contribution 二选一 |
| eft.propagator_corrections | excluded／linearized；组合预测的传播子约定 |
| eft.coefficient_policy | zero_then_set |
| eft.varying_coefficients | 需要独立变化的 Wilson 名列表 |
| eft.width_policy／sm_higgs_loop_vertices | fixed_ufo_defaults／excluded |
| generation.nevents／seed | 正整数：基线事件数与随机种子 |
| generation.beam_energies_GeV／beam_types | 两个正能量；两个 MG lpp 整数，0 为粒子束、1 为质子束 |
| generation.pdf | {label,set_id}；label 为非空字符串，set_id 为非负整数；衰变可给 null |
| generation.cuts | {observable,minimum,maximum,unit} 列表；[] 为明确的空选择 |
| cut.observable | charged_lepton_pt、abs_charged_lepton_eta、abs_charged_lepton_rapidity、dilepton_mass |
| cut 范围／单位 | 非负上下界，可单边；pt／质量用 GeV，η／y 无量纲 |
| simulation.reweighting | {weight_id,contribution?,coefficients?} 列表；每点分别归零 |
| simulation.stage | 任务阶段标签，默认 parton；完整值随原请求保留 |
| production.qcd_order／qed_order | 非负整数，振幅耦合次数上界 |
| production.renormalization_scale_GeV／factorization_scale_GeV | 正数，固定 μR／μF；μF 可给 null |
| production.maxjetflavor／bwcutoff | 非负整数 jet flavour 上限／正数共振区宽度倍数 |

完整名称、坐标、默认值和单位见[模型参数清单导出程序](../scripts/export_model_inventory.py)。各味方案 Wilson external 数为 general 2499、U35 85、MFV 130、top 275、topU3l 182；每种均有两种 EW 输入方案。

安装后导出：`./.venv/bin/python -I -B scripts/export_model_inventory.py .cache/model_inventory.json`。

官方十个 UFO 变体均通过 MadGraph 2.9.27 和 Python 3.10 的双轻子小样本验证。每个变体使用 SM 点生成 100 个事件。 [验证记录](../experiments/ufo_compatibility/summary.json)

MwScheme 以 MW/Gf/aS 为独立输入；alphaScheme 以 aEW/Gf/aS 为独立输入（aEW 为 α）。程序核对 EW 实数域。附加 restriction 会固定或绑定参数，卡片检查按实际导入卡对照。full 沿用 UFO 的味／EW 理论前提。

## 3 实际卡片

| 字段 | 格式／含义 |
|---|---|
| request | 工具 2 的完整原请求 |
| artifacts.param_card／run_card／process_card | 实际文件绝对路径，文件名与任务目录自由选择 |
| artifacts.reweight_card／banner | 可选实际文件；重加权流程由 Agent 核查，banner 用于记录配置对照 |
| expected_intent_sha256 | 可附工具 2 的原请求指纹 |
| phase | configuration 或 run_record；后者核对 banner |
| backend_version | 检查采用的规则版本，默认 2.9.27 |

EFT 阶数组合按有序对核对；固定 μF 使用最终分束流开关。η 对照涉及实际轻子质量，etal 对应 y；通用轻子 cut 的 20 GeV 质量阈值随卡检查。

## 4 运行产物／样本

| 字段 | 格式／含义 |
|---|---|
| event_file | LHE／LHE.gz 的绝对路径 |
| expected_events | 可选正整数，与实际事件数比较 |
| required_weight_ids | 任务要求的权重 ID 列表 |
| run_exit_code | 可选整数，检查是否为 0 |
| log_file | 可选日志绝对路径，附末尾十二行 |

nominal 是 XWGTUP；weights 按 wgt ID 或 initrwgt 对应的压缩权重排列。count、sumw、sumw2 分别是条数、权重和、权重平方和；negative／zero 为符号统计。所有声明、出现及必需 ID 均核对其事件覆盖。截面单列为 init 报告值（pb），权重统计沿用存储约定。events 为正式事件记录数，event_groups 为分组数；init 同时返回 pdf_group_ids／pdf_set_ids。统计与原文件字节指纹同步读取。

## 5 经验准入

| 字段 | 格式／含义 |
|---|---|
| lesson | 一条明确、可复用的经验 |
| applies_to | 非空名称→文本字典，例如 backend／version／model／task |
| evidence[].path | 证据绝对路径 |
| evidence[].sha256 | 可选期望指纹，用于发现证据变化 |
| evidence[].role | observation 引用原始记录；validation 按唯一键、有限数值解析 JSON，优先 status，缺省时用布尔 passed |
| review.by／verdict／reason | 审阅者、accept 或 revise、具体原因；审阅由用户或 Agent 完成 |

输出 admission=ready／review／revise，以及 entry、entry_sha256。entry 包含所读取证据版本的指纹和审阅记录，验证结论与指纹使用同一快照，供 Agent 整理为 memory／skill。

## 6 任务工作流输入与证据

日常使用 `./run.sh chat` 输入自然语言。交互层按最大已有编号递增创建 `workspace/tasks/taskN/taskN.json`，自动填写 task_id、workspace 和 user_request；也可继续使用下述手动入口。

规划阶段的 ask_user 接收 `{question, choices?}`：一个问题及可选选项。回答由窗口写入 user_decisions，恢复后作为 clarifications 交给同一任务的规划阶段；原始请求单独保留。审阅阶段的 deferred 问题也可直接在窗口回答。完成后，图／表／事件文件均位于该 taskN 下，outputs.json 用 plot、table、events、summary、program 字段索引实际位置。

`./run.sh task /绝对路径/task.json` 启动一项任务；`./run.sh resume /绝对路径/task.json` 从保存状态继续。`workspace` 必须是项目 `workspace/` 目录下的绝对路径。仅 task_id、user_request、workspace 为必需字段；省略的 runtime 字段采用下表默认值。

| task.json 字段 | 格式／含义 |
|---|---|
| task_id | 稳定任务名；交互入口自动分配 task1、task2……，手动入口可自填 |
| task_type | 可选任务阶段标签，默认 generation |
| user_request | 完整自然语言请求；Agent 据此整理结构化物理／分析请求 |
| workspace | 项目 workspace/ 下绝对任务目录；交互入口自动填写为 workspace/tasks/taskN |
| runtime.gates_enabled | 布尔，默认true；false用于消融：跳过五个自动检查器及门控修复／审阅，保留通用工具、作业管理和trajectory |
| runtime.max_repairs | 自动修复次数上限，正整数或 null（不设次数上限）；默认 2 |
| runtime.cores | 分配核数，正整数；默认 2 |
| runtime.job_timeout_seconds | 后台作业超时，正整数；默认 3600 |
| user_decisions | 可选主题 → 回答文本映射；交互窗口自动保存规划／审阅问题的回答，也可手动填写后 resume |
| fixtures[] | 可选故障注入；run_card_beam_mismatch 用 field、from、to 在③前改写真实 run_card 一次 |
| experience.draft | 交付后是否整理并准入一条可复用经验 |
| experience.scope | 整理经验所用的 applies_to 范围 |
| analysis | 独立于生成参数；不变质量模板读取observable、pair_pdg_ids、bins、range_GeV；模板默认10个bin。 |

任务工作区布局：

```text
workspace/tasks/<task_id>/
  taskN.json, task.snapshot.json # 交互入口；手动任务可使用 task.json
  outputs.json                  # 最终图、表、事件与说明位置
  request.txt, request.json, defaults.json
  state.json                      # 当前持久化状态快照
  trajectory.jsonl                # schema_version=1; append-only visible execution trace
  trajectory_summary.json         # calls, tokens, stages, repairs, interventions and jobs
  events.jsonl                    # 追加式任务轨迹（节点、检查、工具、作业）
  checkpoints.sqlite              # 逐阶段状态追加历史
  model_trace/                    # calls.jsonl 及各阶段消息记录
  checks/                         # 各检查器按编号与版本的原始结果
  checks/cards_vNN/               # 检查时冻结的卡片副本
  attempts/NNN/                   # cards.mg5、原生过程目录、卡片、run_events.sh
  jobs/<job_id>/                  # job.json、run.sh、job.log、exit_code、finished_epoch
  logs/commands/                  # shell 命令记录
  analysis/                       # analysis.py、analysis_config.json、analysis.json、plot.png、summary.md
  fixtures/NNN/                   # 注入故障记录：before、after、diff
  summary.json                    # 任务最终摘要
memory/candidates/<sha256>.json   # 候选经验，不参与检索
memory/entries/<sha256>.json      # 已准入条目，供后续任务检索
```

Agent finish_stage 约定：

| 字段 | 含义 |
|---|---|
| stage | 本次完成的阶段名，必须与运行阶段一致 |
| complete | 必须为 true；false 视为阶段失败 |
| summary | 简短文字读数，作为 Agent 解释保留 |
| artifacts | 阶段产物绝对路径，位于任务工作区内：request／defaults；param_card／run_card／process_card；plot／table／program／summary；candidate |
| proposed_job | prepare_cards 阶段给出 script（绝对 run_events.sh）、cwd（过程目录）与 expected_outputs |
| dispositions | needs_review 阶段对每类 review_items 给出 {topic, disposition accepted／repaired／deferred, reason, evidence} |

每条处理结论绑定 `check_number`、`check_version`、`check_phase`、`check_sha256` 与覆盖的 `topics`；只有同一检查记录才能复用。accepted 前进；repaired 返回所属阶段（②回规划，③④回卡片准备）、消耗一次修复并复检；deferred 以退出码 5 暂停任务，待 `task.user_decisions` 补齐每个待决主题后 resume。run_record 检查 failed 时一律修复或失败。作业记录含 `started_epoch` 与 `finished_epoch`：生成耗时为两者之差（审计前作业回退使用退出码文件修改时间），检查耗时按完整检查历史求和。经验记录 `experience.stage_usage`：每个操作阶段（prepare_cards、dispositions、analyze_deliver）一条，含条目指纹、是否被采用及阶段摘要；摘要引用条目编号才计为 applied。

finish_stage 无效或缺少某类事项的处理结论时阶段失败；运行器保留现场与一个明确的下一步。任务退出码：0 完成，1 失败，2 任务输入无效，3 等待后台作业（稍后执行 resume），5 等待用户决定（复核处置为 deferred：任务带待决问题暂停，待 `task.user_decisions` 覆盖全部待决主题后经 resume 继续）。


## 分析与运行安排

analysis 独立保存样本／对象、观测量、单位、配对、分析选择、权重 ID、归一化、分箱、sumw/sumw2、相关性、坐标与导出格式。Agent 根据任务执行画图和调试；生成 cut 决定采样相空间，分析选择处理已有事件。

任务执行记录 CPU 分配和作业时限，管理目录、日志、重试与缓存；内存限制由主机或批处理分配提供。模型与卡片指纹用于记录来源；本地官方模型位于 models/smeftsim/official/3.0.2/。

每项任务例行采集 trajectory，无需输入开关；暂停／结束时更新统计，resume 连续追加。原始记录覆盖模型可见消息、工具参数／返回、检查反馈及分支；图片用指纹引用，命令保留日志路径。统计区分工具错误、自动修复、用户回答与外部执行者干预；attempts 为已提交完成的配卡阶段数，未完成阶段按 stage_run 读取。analysis 的 pair_pdg_ids 默认为 [11,-11]，也可用 [23,25] 或 [6,-6]；模板选取两个不同 PDG 编号各一个末态粒子，输出粒子对不变质量。

## Agent 预算设置

| 阶段 | 默认模型调用轮数上限 | 默认单次输出 token 上限 |
|---|---|---|
| plan_resolve（规划） | 30 | 1800 |
| prepare_cards（配卡） | 48 | 2200 |
| dispositions（审阅处置） | 18 | 1400 |
| analyze_deliver（分析交付） | 42 | 3000 |
| experience（经验整理） | 18 | 1600 |

省略 `runtime.agent_budget_multiplier` 即使用上表默认值。显式填写正整数会同时缩放原有的阶段调用上限（按表中顺序为 10、16、6、14、6）和单次输出 token 上限。例如，`1` 使用原有上限，`2` 允许配卡阶段调用 32 轮，单轮最多输出 4,400 token。显式填写 `null` 会取消阶段调用上限，并省略 `max_tokens`，由服务商应用对应模型的输出与上下文限制。

`runtime.max_repairs` 默认为 2，设为 `null` 可取消自动修复次数上限。总 token 按每次 API 返回的输入和输出累加，缓存命中仍计入输入 token。总量没有单独上限。实际生效的限制记录在轨迹的 `stage_budget` 中。测得用量见[审阅说明](REVIEW.zh-CN.md)。

消融试验输入／输出、消耗与贡献核对见[门控消融对比报告](../experiments/gate_ablation/README.zh-CN.md)；失败运行保留现场。
