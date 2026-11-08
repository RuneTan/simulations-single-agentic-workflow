# Linux and Nikhef deployment

[中文](DEPLOYMENT.zh-CN.md) · [README](../README.md)

## Install in the final location

Choose a Linux x86_64 filesystem location with sufficient space for generated processes and events. Keep the checkout in that location after installation: virtual environments and saved tasks contain absolute paths. Clone again and reinstall when changing machines. Task histories belong to the installation that created them.

```bash
git clone https://github.com/RuneTan/simulations-single-agentic-workflow.git
cd simulations-single-agentic-workflow
./scripts/setup.sh
./.venv/bin/python -I -B scripts/check_deployment.py
./run.sh test -q
```

`setup.sh` installs project-owned Python 3.12.12 and 3.10.12, locked Python dependencies, SMEFTsim models and MadGraph. `--python-only` installs only the Agent environment. Downloads need outbound HTTPS. The model and backend installers also accept cached sources; their source manifests remain versioned in `scripts/`.

Load the site's GCC/gfortran/make toolchain before installation and execution. Launchers preserve `PATH` and `LD_LIBRARY_PATH` for site modules while isolating Python imports. `check_deployment.py` checks dependencies and models, then compiles and executes tiny Fortran and C++ programs. `MG5_PYTHON` optionally selects another Python 3.10 executable; the default is the project-managed interpreter.

Copy `.env.example` to `.env.local`, set mode `600`, and edit the key/model fields. Environment variables take priority. The model provider must be reachable from the host running the Agent. Raw trajectories and memory are local runtime data.

## Nikhef execution

Use an interactive Stoomboot node for installation and small checks, and an allocated compute job for sustained generation. Nikhef documents HTCondor, shared storage, the `UseOS` and `JobCategory` settings, and temporary storage via `$TMPDIR`. [Cluster](https://kb.nikhef.nl/ct/Stoomboot_cluster.html) · [Batch jobs](https://kb.nikhef.nl/ct/Batch_jobs.html)

The included example runs the entire workflow in one allocation and waits for its MadGraph subprocesses. Put the checkout on storage visible at the same path from the submission and worker nodes; provision dependencies before submission. This example writes task outputs under the checkout's `workspace/`; for large campaigns, select storage with the required quota and I/O capacity.

```bash
mkdir -p workspace/batch_logs
condor_submit deploy/nikhef/job.sub \
  project_root="$PWD" request_file="$PWD/examples/request.txt"
condor_q
```

The example requests 2 CPUs, 4 GB RAM, an EL9 container and the `short` queue. Keep `request_cpus` and the `--cores` value in `scripts/run_batch.sh` consistent. The script allows a 3-hour generation job inside the 4-hour allocation; planning and analysis also consume time. Add group accounting only if required by your group.

For an initial worker-side check, use `condor_submit -interactive` with the same submit description and run `scripts/check_deployment.py` there. Compiler availability, storage permissions and model-provider access depend on the allocated site environment. Repository portability is tested locally; a Nikhef server run is a separate deployment check.

## Questions and resume

`./run.sh chat` handles questions interactively. A batch task that needs an answer exits with code 5 and stores the question. Answer through `/resume taskN` on an appropriate execution host, or add the topic/answer to `user_decisions` in the saved task JSON and resume with:

```bash
./.venv/bin/python -I -B scripts/run_task.py --resume workspace/tasks/taskN/taskN.json
```

The wrapper waits on existing jobs while it remains in the same allocation. If an allocation is terminated, resume lets the workflow inspect the saved job state and apply its normal repair policy. Exit codes are 0 completed, 1 failed, 2 invalid input, 3 waiting for a job (low-level task API), and 5 awaiting an answer.
