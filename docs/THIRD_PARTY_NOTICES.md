# Third-party material

[中文](THIRD_PARTY_NOTICES.zh-CN.md) · [README](../README.md)

The project has no specified project license. The notices below apply to the named third-party material, independently of the workflow code.

| Source | Material and provenance | Upstream terms |
|---|---|---|
| [MadGraph5_aMC@NLO](https://github.com/mg5amcnlo/mg5amcnlo) | 2.9.27, commit `eb76cab72b8d44aac7162ac7221ac08a4384a169`; installed by checksum. Native run-card defaults, card templates and test cards derive from this format. | [MadTeam NCSA notice](third_party/MadGraph-NCSA.txt) |
| [SMEFTsim](https://github.com/SMEFTsim/SMEFTsim) | 3.0.2, commit `db7d4a80bdcff424eee27dde71f1eb09ac894039`; ten official UFOs installed by checksum. | [MIT notice](third_party/SMEFTsim-MIT.txt) |
| [SMEFTsim June 2023 tutorial](https://github.com/IlariaBrivio/SMEFTsim_tutorial_June23) | Reference UFO, commit `92ffde994d7c0d90554ee629cc477ee171c76b84`; used by the static parameter catalog and legacy default collection. Exact source hashes in `scripts/model_sources.json` and `scripts/reference_model_sources.json`. | SMEFTsim notice above |
| [HEPToolBench](https://github.com/AadarshSingh0/HEPToolBench) | Three benchmark cases and scorers at commit `d3e1ad7fb8392bf27b930f2274fe736fed3b0b21`, retained verbatim under `experiments/heptoolbench/upstream/`. | [Upstream license](../experiments/heptoolbench/upstream/LICENSE) |

Python dependencies, Python interpreters and uv are downloaded during setup and retain their respective upstream licenses. Source manifests and the dependency lock identify the installed versions. Original simulation event files and model-service traces are local runtime data.
