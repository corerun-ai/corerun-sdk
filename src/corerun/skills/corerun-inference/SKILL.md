---
name: corerun-inference
description: Deploy and manage model-serving endpoints on corerun — vLLM for language models, Triton for the rest, LoRA adapters, the publisher's recipe for each model. Use for any request to serve a model, get an OpenAI-compatible endpoint, change how one runs, move it behind another endpoint, or find out why an endpoint is not responding.
---

# corerun inference servers

```bash
corerun inference list                       # add --status running
corerun inference get <server-id>
corerun inference types                      # available engines
corerun inference wait <server-id>           # blocks until running
```

## What there is to deploy

The catalogue lists the models the platform knows something about — context
length, which engine serves them, the memory and cards each build wants — and
the serving images it has:

```bash
corerun catalogue list --search qwen          # --engine vllm, --limit 0 for all
corerun catalogue show <slug-or-model-id>     # builds, memory, cards, features
corerun catalogue engines                     # images, engine versions, CUDA
corerun catalogue check <model-id>            # can the engine's image serve it?
corerun catalogue check <model-id> --compute <cluster>   # ...on that cluster's card
corerun catalogue check <model-id> --image <image>
```

A model need not be in the catalogue to be deployed by its Hugging Face id.
`catalogue check` is decided by the code that refuses a deployment, and exits 1
when the model cannot be served — read its reason (usually an engine too old for
the architecture) before trying another image. With `--compute` it answers for
that cluster: the image its card would be given, whether the card's family needs
a newer engine, and whether a Mac can load the weights at all. `--dry-run` below
goes further, to the arguments the deployment would launch with.

## Deploying

```bash
corerun quota show                           # GPU and server headroom first
corerun compute list                         # valid --compute values
corerun endpoints list                       # the addresses that already exist
corerun inference deploy --name <name> --model <model-id> --compute <target> --dry-run
corerun inference deploy --name <name> --model <model-id> --compute <target> --gpu 1
```

**Look before deploying: `--dry-run`.** It runs every check a deployment makes
and answers with what the server would run — the published recipe found for the
model and how it was found, the engine arguments, the opt-in features the model
offers on this hardware, and what the model's own files say it is — and deploys
nothing. Read it instead of composing engine flags by hand: the recipe carries
the tool-call and reasoning parsers, the KV-cache type and the rest, and they
differ between models of one architecture (Qwen3.5 and Qwen3.8 use different
tool-call parsers).

The recipe is found by the model's id, or — for `--source path` or a name
nobody publishes under — by what its own README and config.json say: the
README's `base_model`, and the checkpoint's precision. If `--dry-run` says
`Recipe: none found`, only the arguments you give are used; say so to the human
rather than guessing flags.

Useful flags: `--type vllm|triton`, `--source huggingface|registry|path`,
`--max-model-len N`, `--gpu-memory-util 0.85`, `--feature <name>`, `--wait`.

**Leave `--quantization` unset** for a checkpoint that declares its own in
config.json — compressed-tensors, NVFP4, FP8, AWQ, GPTQ. The engine reads it from
there; naming a different one is refused. It is only for forcing one.

**Opt-in features** are the ones a model's publisher offers but leaves off —
`spec_decoding` (MTP speculative decoding, offered only when the checkpoint has
an MTP head), `long_context`, `text_only`. Turn one on with `--feature
spec_decoding`; `--dry-run` lists what this model offers here and their
arguments.

A model's sampling defaults in its generation_config.json are applied by the
engine on its own — there is no need to pass them again.

Those three sources are all there is. A model a training run produced reaches
serving through the registry: `corerun runs register` or `corerun models
from-job`, then `--source registry`.

`--endpoint <name>` puts the server behind an existing address instead of
creating one named after the deployment. **When the human names an endpoint,
use it** — list them first with `corerun endpoints list`. An endpoint serves
several models side by side, chosen by the name each call sends: joining one
adds this model beside the others and replaces nothing (`--dry-run` lists what
is already there). Give it a `--served-name` nothing else there answers to. `--served-name` sets
what callers ask for — needed with `--source path`, which would otherwise
publish a directory as the model name. `--arg` passes an engine flag through as
given, repeated per token, and wins over the recipe's.

A model's name is what the human and its README say it is. `model_type` in
config.json is the architecture it descends from (a Qwen3.8 checkpoint says
`qwen3_5`), not its identity — do not correct the human from it.

The address, its keys, and publishing models somebody else runs are the
[corerun-endpoints](../corerun-endpoints/SKILL.md) skill.

**Deploying and scaling up allocate GPUs and hold them until stopped** — an
endpoint is a standing cost, unlike a job that finishes. Confirm the model,
engine, GPU count and compute target with the human before deploying, and never
deploy a second copy of something already running: check `corerun inference list`
first.

## Changing how it runs

```bash
corerun inference plan <server-id>                 # what it runs with, and what it offers
corerun inference update <server-id> --max-model-len 32768 --gpu-memory-util 0.85
corerun inference update <server-id> --feature spec_decoding
corerun inference update <server-id> --add-arg --max-num-seqs --add-arg 16
corerun inference update <server-id> --remove-arg --max-num-seqs
corerun inference update <server-id> --endpoint chat   # move it; no restart
```

The engine reads its flags at start, so an update **redeploys**: the server
keeps its ID, endpoint and keys, but it stops answering while the new one loads
weights. Treat it like a restart — confirm with the human if anyone is calling
it. `--add-arg` and `--remove-arg` change your engine flags and keep the rest;
`--extra-arg`, `--feature` and `--served-name` replace the whole list. The
update prints the resulting list — check nothing was dropped.

`--endpoint` alone is the exception: moving a server behind another endpoint is
routing, takes effect at once and redeploys nothing.

`corerun inference get` shows the image and its engine version, what the model
says about itself, and the engine arguments it runs with — the recipe's and the
card's as well as yours.

## How it is doing

```bash
corerun inference metrics <server-id>              # last 24h
corerun inference metrics <server-id> --range 7d   # 1h, 6h, 24h, 7d, 30d, 90d
```

Requests, failures, tokens in and out, time to first token (p50/p90/p99),
end-to-end latency and decode speed, read from the engine itself. How far back
it goes depends on the organisation's plan; the caption says. Use this before
guessing why callers say it is slow.

## Its key

```bash
corerun inference regenerate-key <server-id>        # prints the new key, once
```

The old key stops working at once, and every caller using it has to switch to
the new one. A running server is redeployed with the new key, so it stops
answering briefly while it loads again (`corerun inference wait <server-id>`);
a stopped one takes the key when it next starts. Callers going through a
model endpoint use the endpoint's key (see
[corerun-endpoints](../corerun-endpoints/SKILL.md)) and are unaffected; rotate
that one instead when the question is a caller's key. Confirm with the human
before regenerating, and hand the key over rather than writing it anywhere.

## Stopping

```bash
corerun inference stop <server-id>           # releases GPUs, keeps the record
corerun inference delete <server-id> --yes   # --yes: there is no prompt without a terminal
```

A server runs as one instance; there is no replica scaling. **Stopping or
deleting an endpoint someone is using is an outage** — confirm with the human
before either, and never do it to free capacity for your own work. A stopped
server does not count against the workspace's server limit.

Without a terminal, a command that asks for confirmation fails with exit code 2
and deletes nothing; pass `--yes` once the human has agreed.

## When an endpoint does not answer

`corerun inference get <server-id>` shows status and the endpoint URL; it reads
the same record as `corerun inference list`, so if the two disagree the server
changed state between them — ask again rather than acting on either. A
server in `pending` or `deploying` is still pulling weights, which takes minutes
for a large model — `corerun inference wait` rather than concluding it is broken.
A `failed` status carries an error field; read it before retrying.

`running` means the port answered, not merely that a container started. A
server that reports running and then fails a request is a different problem
from one that never came up: read the engine's own output rather than the
status.
