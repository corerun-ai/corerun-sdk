# corerun Python SDK

Python SDK and CLI for the corerun ML Platform.

## Installation

The CLI, the corerun skills for your coding agents (Claude Code, opencode, pi,
Codex), and sign-in, in one line -- served by your corerun platform, with its
address and a CLI built from the same commit filled in; the console's Home page
shows it:

```bash
curl -fsSL https://<console>/api/v1/cli/install.sh | sh        # macOS, Linux
irm https://<console>/api/v1/cli/install.ps1 | iex             # Windows (PowerShell)
```

The same scripts are `install.sh` and `install.ps1` here; run from this
repository they install the published CLI and sign in wherever `--url` says.

It installs [uv](https://docs.astral.sh/uv/) if it is missing, then the CLI as a
uv tool, then the skills; on a machine with no display, sign-in shows a code to
approve in any browser (`corerun login --use-device-code`). `--agent claude`
limits the skills to one agent, `--no-skills` skips them. Or step by step:

```bash
uv tool install "git+https://github.com/corerun-ai/corerun-sdk.git"
corerun skills install
corerun login --url https://<console>
```

`pip install "git+https://github.com/corerun-ai/corerun-sdk.git"` does the same.
Not on PyPI yet; the git URL is the install until it is.

From a checkout:

```bash
cd corerun-sdk
pip install -e .
```

## Pointing it somewhere

It talks to `https://corerun.ai/api/v1` unless told otherwise. Everything else
about where a deployment lives, it is told: the platform reports the address
each model endpoint is published at, so a self-hosted deployment needs to set
only the API address in `~/.corerun/config`:

```
api_url=https://corerun.example.com/api/v1
```

`CORERUN_API_URL` overrides the file, which is what CI uses. `corerun login`
writes the file for you.

Model endpoints are called at the address the platform reports for each one,
which it knows better than the SDK can work out. Failing that, the address
follows the API address: `api` under the deployment's own domain, whether the
API was reached at the domain or at its console — `https://corerun.ai/api/v1`
and `https://console.corerun.ai/api/v1` both publish endpoints at
`https://api.corerun.ai`, and an environment named in the host keeps its domain
(`https://console.dev.corerun.ai/api/v1` → `https://api.dev.corerun.ai`).

`CORERUN_INFERENCE_URL` exists for what neither of those can cover: a deployment
whose callers reach the gateway on a host of its own, or behind a private load
balancer. Set, it wins over both and replaces the base an endpoint is called on,
keeping the path and the endpoint's name. `corerun whoami` prints whichever one
is in force.

## Quick Start

### Authentication

Log in once, from a terminal:

```bash
corerun login
```

That opens a browser, and writes the token and workspace to `~/.corerun/config`.
Everything after it — the CLI and the SDK alike — reads that file, so nothing
needs a credential passed to it:

```python
import corerun

corerun.init()          # uses ~/.corerun/config
```

Where there is no browser — a container, a remote box, a notebook on a cluster
— log in the same way and approve it from a machine that has one:

```bash
corerun login --use-device-code
```

It prints a code, you approve it in a browser anywhere, and it writes the same
`~/.corerun/config`. Everything after that works as above, still with no
credential in your code.

A token is for the case where nobody is present to approve anything — a CI job,
a cron. Put it in the environment rather than in a call, so it does not end up
in a file somebody commits:

```bash
export CORERUN_AUTH_TOKEN=cr-xxx
export CORERUN_WORKSPACE=your-workspace-id
```


### Datasets

```python
import corerun

# List datasets
datasets = corerun.datasets.list()
for ds in datasets:
    print(f"{ds.name}: {ds.size_mb:.1f}MB")

# Import from HuggingFace
ds = corerun.datasets.from_huggingface("ylecun/mnist", name="mnist")

# Import from Kaggle
ds = corerun.datasets.from_kaggle("uciml/iris", name="iris")

# View dataset rows
viewer = corerun.datasets.view("mnist", page=1, page_size=10)
print(f"Total rows: {viewer.total_rows}")
for row in viewer.rows:
    print(row)

# Get dataset info
ds = corerun.datasets.get("mnist")
print(f"Size: {ds.size_mb:.1f}MB, Files: {ds.file_count}")

# Delete dataset
corerun.datasets.delete("old-dataset")
```

### Jobs

```python
import corerun

# Submit a training job
job = corerun.jobs.submit(
    name="train-model",
    image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
    compute_name="dgx-cluster",
    command=["python", "train.py"],
    args=["--epochs", "100"],
    gpu=1,
    datasets=["mnist"],
    environment={"WANDB_API_KEY": "xxx"},
)

print(f"Job submitted: {job.id}")

# Wait for completion
job = corerun.jobs.wait(job.id)
print(f"Job finished with status: {job.status}")

# Get logs
logs = corerun.jobs.logs(job.id)
print(logs)

# List running jobs
running = corerun.jobs.list(status="running")

# Cancel a job
corerun.jobs.cancel(job.id)

# Run and wait (convenience function)
job = corerun.jobs.run(
    name="quick-task",
    image="python:3.11",
    compute_name="cpu-cluster",
    command=["python", "-c", "print('Hello')"],
)
```

### Jobs with Local Training Code

Upload local training code to run on remote compute clusters:

```python
import corerun

# Submit job with local source code
job = corerun.jobs.submit(
    name="train-custom-model",
    image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
    compute_name="dgx-cluster",
    source_directory="./src",  # Upload local code
    command=["python", "train.py"],
    gpu=1,
    datasets=["mnist"],
    experiment="my-experiment",
)

# Code is uploaded to storage and mounted at /code
# Working directory is automatically set to /code
# CORERUN_CODE_DIR=/code is set in environment

# Run and wait for completion
job = corerun.jobs.run(
    name="train-and-wait",
    image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
    compute_name="dgx-cluster",
    source_directory="./training",
    command=["python", "train.py", "--epochs", "10"],
    gpu=1,
    timeout=3600,  # 1 hour timeout
)
print(f"Training completed with status: {job.status}")
```

Files excluded by default when uploading source code:

- `__pycache__`, `*.pyc`, `.git`, `.venv`, `venv`, `node_modules`
- `.env`, `*.log`, `.coverage`, `dist`, `build`

Override with `exclude_patterns`:

```python
job = corerun.jobs.submit(
    name="train",
    image="python:3.11",
    compute_name="cpu-cluster",
    source_directory="./src",
    exclude_patterns=["*.pyc", "__pycache__", "tests"],  # Custom patterns
    command=["python", "train.py"],
)
```

### Model Registry

```python
import corerun

# List registered models
models = corerun.registry.list_models()
for model in models:
    print(f"{model.name}: {model.version_count} versions")

# Create a new model
model = corerun.registry.create_model(
    "resnet50",
    description="ResNet-50 image classifier",
    tags={"framework": "pytorch", "task": "classification"},
)

# Record a version. The files are already in workspace storage -- moving them
# in and out is the CLI's job, because it needs git-lfs:
#   corerun models push resnet50 ./checkpoint -f pytorch
#   corerun models pull resnet50 --alias champion
version = corerun.registry.create_version(
    "resnet50",
    storage_path="models/resnet50/1",
    framework="pytorch",
    description="Trained on ImageNet",
)
print(f"Recorded as v{version.version}")

# Promote it
corerun.registry.set_version_stage("resnet50", version.version, "production")

# Point an alias at it
corerun.registry.set_alias("resnet50", "champion", version.version)
corerun.registry.set_alias("resnet50", "latest", version.version)

# List versions
versions = corerun.registry.list_versions("resnet50")
for v in versions:
    print(f"v{v.version}: {v.stage}")

# List aliases
aliases = corerun.registry.list_aliases("resnet50")
for alias in aliases:
    print(f"@{alias.alias} -> v{alias.version}")

# Delete an alias, and a whole model
corerun.registry.delete_alias("resnet50", "latest")
corerun.registry.delete_model("old-model")
```

A model whose weights are on HuggingFace can be imported without pulling them
through this machine:

```python
corerun.registry.import_from_huggingface(
    "llama-3-8b",
    huggingface_id="meta-llama/Meta-Llama-3-8B",
    revision="main",
)
```

Serving a registered model is a deployment, not a local prediction -- see
`corerun inference deploy`.


## CLI Usage

### Authentication

```bash
# Login (saves credentials to ~/.corerun/config)
corerun login --workspace your-workspace-id

# Interactive login
corerun login

# Check status
corerun whoami

# Logout
corerun auth logout
```

### Datasets

```bash
# List datasets
corerun datasets list

# Get dataset details
corerun datasets get mnist

# View dataset rows
corerun datasets view mnist --split train --page 1 --size 10

# Import from HuggingFace
corerun datasets import huggingface ylecun/mnist
corerun datasets import huggingface ylecun/mnist --name my-mnist

# Import from Kaggle
corerun datasets import kaggle uciml/iris --name iris

# Import from URL
corerun datasets import url https://example.com/data.zip --name my-data

# Delete dataset
corerun datasets delete old-dataset
corerun datasets delete old-dataset --force
```

### Jobs

```bash
# List jobs
corerun jobs list
corerun jobs list --status running
corerun jobs list --compute dgx-cluster

# Submit a job
corerun jobs submit \
    --name train-model \
    --image pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime \
    --compute dgx-cluster \
    --gpu 1 \
    --datasets mnist \
    --command "python train.py"

# Submit with local training code
corerun jobs submit \
    --name train-custom \
    --image pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime \
    --compute dgx-cluster \
    --source ./src \
    --command "python train.py" \
    --gpu 1

# Submit and wait
corerun jobs submit \
    --name train \
    --image python:3.11 \
    --compute cpu-cluster \
    --command "python train.py" \
    --wait

# Get job details
corerun jobs get abc123

# Get logs
corerun jobs logs abc123
corerun jobs logs abc123 --tail 100
corerun jobs logs abc123 --follow

# Wait for job
corerun jobs wait abc123
corerun jobs wait abc123 --timeout 3600

# Cancel job
corerun jobs cancel abc123

# Delete job
corerun jobs delete abc123
```

### Model Registry

```bash
# List models
corerun models list
corerun models list --json

# Get model details
corerun models get my-model

# Create a new model
corerun models create my-model --description "My ML model"
corerun models create my-model -t framework=pytorch -t task=classification

# Upload a model file
corerun models push my-model ./checkpoint -f pytorch
corerun models push my-model ./checkpoint -d "Trained for 100 epochs"

# Download a model
corerun models pull my-model --version 1
corerun models pull my-model --alias champion

# List versions
corerun models versions my-model

# Transition stage
corerun models stage my-model 1 staging
corerun models stage my-model 1 production
corerun models stage my-model 1 archived

# Manage aliases
corerun models alias my-model champion 1
corerun models alias my-model challenger 2
corerun models aliases my-model
corerun models unalias my-model challenger

# Delete model
corerun models delete old-model
corerun models delete old-model --force
```

### Serving

```bash
# Deploy a model; it is published behind an endpoint named after it
corerun inference deploy --name nova --model Qwen/Qwen3-8B --compute dgx --gpu 1 --wait

# Change how it runs (redeploys; the ID, endpoint and keys stay)
corerun inference update <server-id> --max-model-len 32768 --gpu-memory-util 0.85

# Requests, tokens, time to first token and decode speed
corerun inference metrics <server-id> --range 7d

# The address callers use, and calling it
corerun endpoints show nova
corerun endpoints call nova "Hello" --stream

# Publish a provider's model on the same address, and change it later
corerun endpoints add-upstream nova --model chat --base-url https://api.openai.com/v1 --api-key sk-... --as gpt-4o
corerun endpoints edit-upstream nova chat --upstream-name gpt-4.1

# Priced from the provider's list price when added by provider; override per endpoint or organisation
corerun endpoints add-upstream nova --model gpt-4o --provider openai --api-key sk-...
corerun prices search openai gpt-4o
corerun endpoints price nova chat --input 2.5 --output 10 --cache-read 1.25
corerun org prices set openai gpt-4o --input 2 --output 8
corerun endpoints metrics nova
```

### Your organisation

```bash
# The plan, what is used against it, and model calls, tokens and spend per endpoint this month
corerun org usage

# Make signing in stricter: lifetimes, lockout, passwords, networks, countries
corerun org security show
corerun org security set --access-ttl 30m --lockout-attempts 5
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CORERUN_AUTH_TOKEN` | Auth token, for when a browser login is not possible |
| `CORERUN_WORKSPACE` | Default workspace ID |
| `CORERUN_API_URL` | API base URL (default: https://corerun.ai/api/v1) |
| `CORERUN_INFERENCE_URL` | Where model endpoints are published, when it is not what the platform reports |
| `CORERUN_TIMEOUT` | Request timeout in seconds (default: 30) |
| `CORERUN_VERIFY_SSL` | Check TLS certificates (default: true; set false for a self-signed deployment) |
| `CORERUN_AUTH_TOKEN_FILE` | Where to read the platform credential (default: `/etc/corerun/api-key`) |

Each one overrides the same setting in `~/.corerun/config`, which in turn
overrides the default.

### Inside a corerun notebook

A notebook is launched with a credential of its own at `/etc/corerun/api-key`,
so the SDK and CLI work with no setup. The key is short-lived and the platform
replaces the file every few hours while the notebook runs — the SDK reads it
when it needs it, and re-reads it after a 401, so a renewal is invisible.

It is the last source consulted, so anything you choose yourself — `corerun
login`, `CORERUN_AUTH_TOKEN`, an explicit `api_key=` — keeps your own identity.

### Config File

Credentials can be saved to `~/.corerun/config`:

```ini
api_key=cr-xxx
workspace=your-workspace-id
api_url=https://dev.corerun.ai/api/v1
timeout=30
```

## Error Handling

```python
from corerun.exceptions import (
    CoreRunError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
)

try:
    ds = corerun.datasets.get("nonexistent")
except NotFoundError:
    print("Dataset not found")
except AuthenticationError:
    print("Invalid API key")
except CoreRunError as e:
    print(f"API error: {e}")
```

## License

Apache-2.0
