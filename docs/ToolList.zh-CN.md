# 独立检查器

[English](ToolList.md) · [README](../README.zh-CN.md) · [架构](REVIEW.zh-CN.md) · [参数](ParaList.zh-CN.md)

工作流自动调用以下五个模块，也可通过 CLI 独立检查已有文件。

| 编号／命令 | 功能 | 输入 | 输出 |
|---|---|---|---|
| 1 inspect | 查询环境并比较期望版本 | 可省略；包／后端名称与期望版本 | 实际版本、路径、通过项和差异 |
| 2 validate-spec | 整理 SM／EFT 点并检查参数一致性 | simulation，可附 production／analysis／自由扩展 | 原请求、指纹、默认值来源、逐点系数与输入差异 |
| 3 validate-cards | 对照请求检查实际卡片 | request、卡片绝对路径，可附 banner 与请求指纹 | 参数、贡献阶数、尺度、cut 和记录配置的对照 |
| 4 check-sample | 按初始化与事件层级逐记录读取 LHE／LHE.gz | event_file，可给预期事件数、权重 ID、退出码与日志 | 实际事件数、nominal／各权重的 sumw、sumw2、负权重数、日志末尾 |
| 5 check-experience | 整理可复用经验并核对证据与审阅 | lesson、applies_to、evidence、review | ready／review／revise、带证据指纹的 entry 与条目指纹 |

工具 3 在生成前比较卡片（`configuration`），生成后比较记录的 banner（`run_record`），后者读取 MG5 2.9.27 的 CDATA 包裹和列表型设置。工具 4 区分存储的事件权重与 init 中报告的截面；工具 5 将经验绑定到实际提供的证据版本和复核记录。

覆盖范围是环境身份、SMEFTsim 独立输入、MG5 2.9.27 配置、LHE 结构与统计、经验的证据。Agent 和用户评估物理解释及需求匹配。

## 运行小型示例

文件检查器使用绝对路径。下面的命令用当前仓库路径填充模板，并生成两个事件的样本检查报告：

```bash
./.venv/bin/python -I -B scripts/prepare_examples.py
./run.sh validate-spec examples/request_sm.json
./run.sh validate-cards .cache/examples/check_existing_cards.json
./run.sh check-sample .cache/examples/check_sample.json
./run.sh check-experience .cache/examples/check_experience.json
```

`examples/` 中的 JSON 使用 `{PROJECT_ROOT}` 和 `{EXAMPLE_OUTPUT}` 作为模板标记。`prepare_examples.py` 将可直接运行的输入写入 `.cache/examples/`，检查器 API 接收明确的文件路径。完整格式、单位和默认值见参数表。
