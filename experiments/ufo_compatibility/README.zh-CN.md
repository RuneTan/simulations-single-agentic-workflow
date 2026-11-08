# UFO 的 Python 3 兼容性

[English](README.md) · [实验](../README.zh-CN.md)

十个官方 SMEFTsim 3.0.2 变体均在 MadGraph 2.9.27 和 Python 3.10 下各生成了 100 个 SM 双轻子事件。检查包括束流记录、请求的种子与切选、事件数、末态粒子、有限正截面，以及运行前后全部 170 个固定 UFO 源文件。[逐模型结果](summary.json)

重新执行并保存新的本地证据：

```bash
./.venv/bin/python -I -B scripts/verify_ufo_runtime.py experiments/ufo_compatibility/runs/new-run
```

这是每个变体一个 SM 点的后端运行测试，其他过程与 EFT 点通过各自任务证据检验。
