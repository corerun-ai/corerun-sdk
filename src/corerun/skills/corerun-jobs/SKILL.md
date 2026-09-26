---
name: corerun-jobs
description: Submit, monitor, and debug training jobs on corerun — run a folder of training code on a GPU profile, from flags or a corerun.yaml job file, list jobs, read logs, check status, stop or delete them. Use for any request to train a model, run a training script, check why a job failed, or find what is currently running.
---

# corerun jobs

```bash
corerun jobs list                     # everything in the workspace
corerun jobs list --status running
corerun jobs get <job-id>
corerun jobs logs <job-id>            # add --follow to stream
corerun jobs wait <job-id>
corerun jobs cancel <job-id>
```

## Running training code

A job is **a folder of code, an image, and a command**, the way Ray
(`--working-dir`), SageMaker (`source_dir`) and SkyPilot (`workdir`) do it. The
folder does not need to be a git repository; it is snapshotted on each submit
and appears in the job as `/code`, the working directory.

```bash
corerun compute list                  # valid --compute values
corerun clusters list                 # each cluster's profiles
corerun quota show                    # GPU and job headroom

cd my-project
corerun jobs submit --name train --image <image> --compute <target> --profile <profile> \
  --source . -- python train.py --epochs 10
```

Everything after `--` is the command, kept exactly. Inside the job:

- `requirements.txt` in the folder is installed before the command
  (`--no-requirements` to skip);
- datasets from `--datasets a,b` are read-only under `/data/<dataset>`;
- results go to `$CORERUN_OUTPUT_DIR` and checkpoints to
  `$CORERUN_CHECKPOINT_DIR` — both are kept in the workspace's storage;
  `$CORERUN_SCRATCH_DIR` is not.

The folder's `.gitignore` is honoured. A folder with files over 25 MB, or 90 MB
in all, is refused with the files named: data belongs in a dataset, weights in
the model registry — add them to `.gitignore`, do not raise the limit.

### A job file

Write the job down as `corerun.yaml` in the folder (SkyPilot's task format)
when it will be run more than once:

```yaml
name: train
workdir: .
image: <image>
resources: {compute: <target>, profile: <profile>, max_runtime: 6h}
envs: {EPOCHS: "10"}
datasets: [imagenet]
run: python train.py --epochs $EPOCHS
```

```bash
corerun jobs submit -f corerun.yaml              # flags override the file
```

`setup:` runs before `run:` and replaces the automatic requirements install.

### Code already in a repository

```bash
corerun jobs submit ... --git-url acme/trainer --connection github --ref v2 -- python train.py
corerun repos push trainer ./my-project          # the workspace's own repositories
corerun jobs submit ... --repo trainer --ref main --path src -- python train.py
```

A private repository on GitHub or another host needs a git connection
(Workspace settings → Integrations); a public https URL needs none. In a job
file, `repo: {name, ref, path}` or `git: {url, connection, ref, path}` replaces
`workdir`. `--priority low|normal|high` and `--max-runtime <minutes>` are
optional.

**Submitting allocates GPUs and costs money.** Confirm the image, command,
dataset and GPU count with the human before submitting anything — and never
submit a speculative job to see what happens.

## Debugging a failure

Read the logs before theorising: `corerun jobs logs <job-id>` returns the tail,
which usually carries the traceback. A failure in `pip install` is a
requirements.txt problem, not the training code. `corerun jobs get <job-id>`
shows the config it actually ran with, which is where a wrong dataset path or
missing environment variable shows up.

A job that never leaves `pending` is usually waiting on capacity rather than
broken — check `corerun quota gpus` and `corerun clusters list` before
concluding anything about the code.
