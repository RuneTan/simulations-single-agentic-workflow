# Linux 与 Nikhef 部署

[English](DEPLOYMENT.md) · [README](../README.zh-CN.md)

## 在最终目录安装

选择有足够空间存放生成过程和事件的 Linux x86_64 文件系统目录。安装后保持目录位置：虚拟环境和已保存任务包含绝对路径。更换机器时重新下载、安装；任务历史属于创建它的那份安装。

```bash
git clone https://github.com/RuneTan/simulations-single-agentic-workflow.git
cd simulations-single-agentic-workflow
./scripts/setup.sh
./.venv/bin/python -I -B scripts/check_deployment.py
./run.sh test -q
```

`setup.sh` 安装项目独立的 Python 3.12.12 和 3.10.12、锁定的 Python 依赖、SMEFTsim 模型及 MadGraph；`--python-only` 仅安装 Agent 环境。下载需要 HTTPS 访问。模型和后端安装器也支持缓存来源，版本清单保存在 `scripts/`。

安装与运行前加载站点的 GCC/gfortran/make 工具链。启动入口保留站点模块的 `PATH` 与 `LD_LIBRARY_PATH`，同时隔离 Python 导入。`check_deployment.py` 检查依赖和模型，并实际编译、执行小型 Fortran 与 C++ 程序。可用 `MG5_PYTHON` 指定另一份 Python 3.10；默认使用项目独立安装的解释器。

复制 `.env.example` 为 `.env.local`，设置权限 `600`，填写密钥和模型名称。环境变量优先。运行 Agent 的节点需要能够访问模型服务。原始轨迹和记忆保存在本地运行目录。

## Nikhef 执行

在 Stoomboot 交互节点安装和进行小型检查，持续仿真放到分配的计算作业中。Nikhef 官方说明包含 HTCondor、共享存储、`UseOS`、`JobCategory` 和 `$TMPDIR` 临时目录规则。[集群](https://kb.nikhef.nl/ct/Stoomboot_cluster.html) · [批处理](https://kb.nikhef.nl/ct/Batch_jobs.html)

附带示例在一个分配中运行整个工作流，并等待其 MadGraph 子进程。仓库应位于提交节点和工作节点以相同路径可见的存储，依赖在提交前安装。示例将任务产物写入仓库的 `workspace/`；大型实验需选择配额和 I/O 能力适当的存储。

```bash
mkdir -p workspace/batch_logs
condor_submit deploy/nikhef/job.sub \
  project_root="$PWD" request_file="$PWD/examples/request.txt"
condor_q
```

示例申请 2 个 CPU、4 GB 内存、EL9 容器和 `short` 队列。调整 CPU 时同步修改 `request_cpus` 和 `scripts/run_batch.sh` 中的 `--cores`。生成作业时限为 3 小时，整个分配为 4 小时，规划与分析也会占用时间。课题组有要求时再添加组账户字段。

首次可使用同一提交描述通过 `condor_submit -interactive` 获取工作节点环境，并在那里运行 `scripts/check_deployment.py`。编译器、存储权限和模型服务访问取决于实际分配的环境。仓库可迁移性在本地测试，Nikhef 上的实机运行属于部署后的单独检查。

## 提问与继续执行

`./run.sh chat` 在窗口中处理提问。批任务需要回答时以退出码 5 保存问题并暂停。在适当的执行节点用 `/resume taskN` 回答，或在已保存任务 JSON 的 `user_decisions` 中填写主题与答案，然后运行：

```bash
./.venv/bin/python -I -B scripts/run_task.py --resume workspace/tasks/taskN/taskN.json
```

批入口会在同一分配内等待已有作业。分配终止后，继续执行会检查保存的作业状态并采用正常修复策略。退出码为：0 完成、1 失败、2 输入错误、3 等待作业（底层 task API）、5 等待回答。
