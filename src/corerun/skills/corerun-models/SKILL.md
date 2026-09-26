---
name: corerun-models
description: Work with the corerun model registry — list and create registered models, import from HuggingFace, push and pull versions, stage them through staging and production, and set aliases like champion. Use when asked to register, import, promote, fetch or version a model.
---

# corerun model registry

```bash
corerun models list
corerun models get <name>
corerun models versions <name>
corerun models create <name> --framework <framework>
corerun models delete <name>
```

## Getting a model in

Importing is the usual way, and the platform does the transfer — weights go
from the source straight into the registry without passing through this
machine. That is what makes it work from a shell that could not hold the file.

```bash
corerun models import meta-llama/Meta-Llama-3-8B
corerun models import meta-llama/Meta-Llama-3-8B --name llama3 -r main
HF_TOKEN=hf_... corerun models import <gated-model>       # or --token
corerun models import <model> --no-wait                    # start it, get the shell back
```

`--local` inverts it: download here, push from here. Only reach for it when the
source is reachable from this machine and not from the platform.

Pushing a directory is the other way in — a model this platform trained, or one
built locally:

```bash
corerun models push <name> ./checkpoint -f pytorch
```

## Getting a model out

```bash
corerun models pull <name>
corerun models pull <name>@1 ./dest
corerun models pull <name> --alias champion
```

`corerun models push` and `pull` need only **`git`** on the machine: the CLI
moves the weights itself — large files in parts, so any size gets through,
downloads in parallel ranges that resume. `git-lfs` is needed only to work on
a model repository with plain git, and for `corerun models import --local`.

The credential is the one from `corerun login`; nothing separate is issued. It
goes through the platform's git proxy, which checks access per request, and is
sent as a header rather than in the URL so it does not end up written into
`.git/config`.

## Stages and aliases

```bash
corerun models stage <name> <version> staging
corerun models stage <name> <version> production
corerun models alias <name> champion <version>
corerun models aliases <name>
corerun models unalias <name> champion
```

Stages are `none`, `staging`, `production`, `archived` — there is no
development stage. An alias is a moving pointer, so `champion` can be repointed
without callers changing anything; prefer aliases over version numbers when
wiring a model into a serving path.

They answer different questions and do not move together. Promoting to
production does not repoint `@champion`, and repointing `@champion` does not
promote anything. If the intent is "make this the one that serves traffic", say
which of the two was meant and do that one.

**Promoting to production changes what live traffic gets.** Confirm the version
with the human, and say which version it replaces. Never promote a version you
have not seen evaluated.

## Deleting

`corerun models delete <name>` removes the model and every version of it.
Anything serving that model keeps running on what it already loaded and cannot
be redeployed. Check `corerun endpoints list` before deleting.
