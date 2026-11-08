# 工作流与实验复核

[English](REVIEW.md) · [使用入口](../README.zh-CN.md) · [工具](ToolList.zh-CN.md) · [参数](ParaList.zh-CN.md) · [进度](PROGRESS.zh-CN.md) · [实验](../experiments/README.zh-CN.md)

## 工作流

单 Agent 负责理解请求、配卡、调用 MadGraph、分析和解释。LangGraph 管理任务状态、后台作业和阶段交接。五个检查器分别检查环境、请求、实际卡片与 banner、样本和经验准入。卡片与事件的判定依据见工具清单和参数表。

```mermaid
flowchart LR
    A[请求与工作区] --> B[环境与请求检查]
    B --> C[配卡和复核]
    C --> D[启动作业与等待]
    D --> E[核对样本与 banner]
    E --> F[分析与交付]
    F --> G[按需检查经验候选]
```

检查成功后推进。可修复的问题回到规划或配卡并复检。`needs_review` 由 Agent 针对对应检查编号、版本、阶段和指纹逐项处理。`accepted` 推进，`repaired` 复检，`deferred` 保存问题并等待用户。默认允许两次自动修复，完整预算见参数表。当前生成流程的交付包括事件、图、表、分析程序和说明。

## 执行与复核

1. 日常用 `./run.sh chat`。批跑用 `create_task`、`run_task` 和 `run_resume`。新任务自动分配独立工作区，生成设置和分析设置分别记录。
2. 退出码 3 表示等待已有作业，恢复时查询同一 `job_id`。退出码 5 表示等待回答，补齐 `user_decisions` 后继续。任务状态、检查点和日志均留在对应任务目录。
3. 比较实验时固定代码、原始请求和配对预算。`runtime.gates_enabled` 默认 `true`，`false` 用于移除自动检查及其反馈、审阅和修复的消融。任务由项目内 Agent 运行和修复，外部评分器读取产物。
4. 先读 `outputs.json`、`summary.json` 和 `trajectory_summary.json`。按需复读原生卡片、banner、LHE、直方图及 `trajectory.jsonl`。分别报告运行状态、请求符合度、数值和解释。API 的输入 token 已包含缓存命中。复算费用与原任务费用分别统计。
5. 共享经验先检索，写入前由⑤检查并显式保存。并行运行时按过程、参数点或随机种子划分工作区，最终分析等相应样本就绪。

```bash
./run.sh test -q
./.venv/bin/python -I -B scripts/check_deployment.py
```

## 门控消融

| 指标 | 有门控 task1–4 | 无门控 task5–8 |
|---|---:|---:|
| 运行完成 | 4/4 | 2/4 |
| 生成设置符合请求，且数值复算一致 | 4/4 | 0/4 |
| 合计 token | 639,321 | 525,994 |

四对分别是 SM 双轻子、`clq1` 双轻子、`cHW` 的 \(Zh\) 和 `cuGRe` 顶对。每项目标 20,000 事件。task5 配卡预算耗尽，task6 后端生成失败，task7／8 各生成 20,000 个纯维六平方项事件，偏离组合预测请求。事后用实际 banner 重放卡片检查③得到 `needs_review`，这一差异当时交给 Agent 判断。task1 的卡片和样本核对通过，文字中的“辐射尾”解释有误。

四个过程各跑一次配对，使用当时各任务的预算。每个过程另有 100,000 个独立配卡、独立种子的同设置 LO 参考事件。成本与输入输出、五张图、复算数据集中在实验目录，原始轨迹在本地保留。[门控消融资料](../experiments/gate_ablation/README.zh-CN.md)

## 外部 benchmark 和后端验证

HEPToolBench 三道 MadGraph 卡片题各跑一次有门控和无门控。六轮均未交付独立卡片。后端完成数分别为 2/3 和 3/3。task17 的实际 banner 符合六项设置，官方评分为 1.00。现有生成流程把卡片请求继续推进到仿真和分析。task16 在 `U35_alphaScheme` 的模型导入处失败。随后单独扩展 Python 3 兼容层，十个官方 UFO 变体各完成 100 个 SM 双轻子事件的运行检查。[HEPToolBench 报告](../experiments/heptoolbench/README.zh-CN.md) · [UFO 运行验证](../experiments/ufo_compatibility/summary.json)
