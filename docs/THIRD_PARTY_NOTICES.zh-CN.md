# 第三方材料说明

[English](THIRD_PARTY_NOTICES.md) · [README](../README.zh-CN.md)

本项目未指定项目许可证。以下声明分别适用于列明的第三方材料，与工作流代码本身分开。

| 来源 | 材料与版本 | 上游条款 |
|---|---|---|
| [MadGraph5_aMC@NLO](https://github.com/mg5amcnlo/mg5amcnlo) | 2.9.27，提交 `eb76cab72b8d44aac7162ac7221ac08a4384a169`；按校验和安装。原生 run-card 默认值、卡片模板和测试卡片来自该格式。 | [MadTeam NCSA 声明](third_party/MadGraph-NCSA.txt) |
| [SMEFTsim](https://github.com/SMEFTsim/SMEFTsim) | 3.0.2，提交 `db7d4a80bdcff424eee27dde71f1eb09ac894039`；十个官方 UFO 按校验和安装。 | [MIT 声明](third_party/SMEFTsim-MIT.txt) |
| [SMEFTsim 2023 年 6 月教程](https://github.com/IlariaBrivio/SMEFTsim_tutorial_June23) | 参考 UFO，提交 `92ffde994d7c0d90554ee629cc477ee171c76b84`；用于静态参数清单和既有默认模型集合。文件哈希见 `scripts/model_sources.json` 与 `scripts/reference_model_sources.json`。 | 上述 SMEFTsim 声明 |
| [HEPToolBench](https://github.com/AadarshSingh0/HEPToolBench) | 三个 benchmark 用例与评分器，提交 `d3e1ad7fb8392bf27b930f2274fe736fed3b0b21`；原样保存在 `experiments/heptoolbench/upstream/`。 | [上游许可证](../experiments/heptoolbench/upstream/LICENSE) |

Python 依赖、Python 解释器和 uv 在安装时下载，各自保留其上游许可证。来源清单与依赖锁定文件标识具体版本。原始仿真事件和模型服务轨迹属于本地运行数据。
