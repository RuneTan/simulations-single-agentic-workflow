# Simulations Single Agentic Workflow

[English](README.md) · [架构与结果](docs/REVIEW.zh-CN.md) · [参数](docs/ParaList.zh-CN.md) · [检查器](docs/ToolList.zh-CN.md) · [Linux / Nikhef 部署](docs/DEPLOYMENT.zh-CN.md) · [实验](experiments/README.zh-CN.md)

一个 Agent 将自然语言物理请求转化为 MadGraph 仿真和分析。LangGraph 管理执行阶段与持久状态，五个独立脚本在对应交接处检查环境、请求、原生卡片、事件样本和经验条目。

Agent 使用通用文件和命令工具选择模型、配置卡片、生成事件、画图并修复问题。每个任务保存输入、检查反馈、可见执行轨迹、token 用量和输出。

## 快速开始

需要 Linux x86_64、Bash、curl、tar、make、g++ 和 gfortran。安装程序创建独立 Python 环境，并下载带校验和的固定版本模型与后端；这些项目依赖的安装无需管理员权限。

```bash
git clone https://github.com/RuneTan/simulations-single-agentic-workflow.git
cd simulations-single-agentic-workflow
./scripts/setup.sh
cp .env.example .env.local
chmod 600 .env.local
# 编辑 .env.local，填写自己的模型 API 密钥和模型名称。
./run.sh chat
```

当前模型客户端使用 DeepSeek 的 chat-completions API；通过环境变量或 `.env.local` 配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。示例不含凭据，实际模型调用产生供应商用量。

直接输入中文或英文请求。`/tasks` 查看任务，`/resume task1` 继续任务，`/quit` 退出。规划时 Agent 可以在同一窗口提问。完整请求示例见 [examples/request.txt](examples/request.txt)。

```bash
./run.sh inspect
./run.sh test -q
./.venv/bin/python -I -B scripts/run_task.py --request-file examples/request.txt
```

## 输入与输出

生成设置描述过程、SMEFTsim 模型、参数点、耦合阶、束流、PDF、切选、事件数和种子；分析设置描述可观测量、粒子配对、筛选、分箱和归一化。仅指定过程时从 SM 点出发，其余仿真设置由 Agent 记录默认选择或向用户询问。

任务自动编号并保存在 `workspace/tasks/taskN/`。`outputs.json` 索引 LHE 样本、图、数值表、分析程序和总结；`trajectory.jsonl` 与 `trajectory_summary.json` 记录可见决策、工具调用、检查、修复、耗时和 API token 用量。继续执行会向同一任务追加记录。

当前生成流程交付样本与分析，独立配卡交付是后续扩展方向。检查器负责明确配置与数值一致性，物理解释由 Agent 和用户复核。通用命令以运行工作流的操作系统账号权限执行。

## 仓库结构

| 位置 | 用途 |
|---|---|
| `src/nsbi_agent/workflow/` | LangGraph 阶段、模型客户端、工具、作业、状态与轨迹 |
| `src/nsbi_agent/tools/` | 五个独立检查器 |
| `src/nsbi_agent/interaction/` | 任务分配与交互提问 |
| `scripts/` | 安装、后端启动与可复用验证 |
| `tests/` | 模块化回归与小型独立测试样例 |
| `examples/` | 自然语言请求与检查器输入模板 |
| `docs/` | 中英文架构、参数与部署说明 |
| `experiments/` | 公开结果数据、图和 benchmark 适配程序 |
| `deploy/nikhef/` | HTCondor 提交示例 |

安装生成 `.venv/`、`.tools/`、`models/` 和 `backends/`，运行生成 `workspace/` 与 `memory/`。这些目录、本地凭据和原始任务轨迹均不进入 Git。

## 可复现设置

Python 依赖由 `uv.lock` 锁定。Agent 使用 Python 3.12.12，MadGraph 2.9.27 使用单独的 Python 3.10.12。安装涵盖 SMEFTsim 3.0.2 的十个官方变体，以及维持现有过程默认值的固定参考模型。限定范围的导入兼容层在内存中转换旧 UFO 支持代码，保留模型源文件哈希。

[实验索引](experiments/README.zh-CN.md)区分历史试验结果与当前可迁移性检查；[进度](docs/PROGRESS.zh-CN.md)记录本次发布改动。

本项目未指定许可证。第三方声明只适用于列明的上游材料与依赖，见[来源说明](docs/THIRD_PARTY_NOTICES.zh-CN.md)。
