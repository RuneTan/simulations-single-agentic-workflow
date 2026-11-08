# UFO Python 3 compatibility

[中文](README.zh-CN.md) · [Experiments](../README.md)

All ten official SMEFTsim 3.0.2 variants generated 100 SM dilepton events each with MadGraph 2.9.27 and Python 3.10. The checks covered beam records, requested seed/cuts, event count, final-state particles, finite positive cross sections, and all 170 pinned UFO source files before and after generation. [Per-model results](summary.json)

Repeat with fresh local evidence:

```bash
./.venv/bin/python -I -B scripts/verify_ufo_runtime.py experiments/ufo_compatibility/runs/new-run
```

This is a backend execution test at one SM point per variant. Other processes and EFT points are tested through their own task evidence.
