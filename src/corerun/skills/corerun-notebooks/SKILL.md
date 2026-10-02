---
name: corerun-notebooks
description: Start, find, share, move, stop and delete notebooks on corerun — interactive sessions on a cluster or bare-metal host, with GPUs, mounted datasets and environment variables — and read a stopped notebook's saved files. Use when asked for a notebook or a place to work interactively, for a notebook's URL, to share one, to move one to other compute or onto a GPU, to see what a stopped one contains, or to free the GPUs a notebook is holding.
---

# corerun notebooks

```bash
corerun notebooks list                      # add --status running
corerun notebooks get <notebook>            # status, compute, image, GPUs, datasets, URL,
                                            # where its home is, what a start is doing
corerun notebooks url <notebook>            # the URL alone, for piping or opening
```

A notebook is named by its name or its id — the short id the list prints
works too. An ambiguous name is refused with the ids to choose from.

## Starting one

```bash
corerun quota show                          # notebook and GPU headroom first
corerun compute list                        # valid --compute values
corerun notebooks create <name> --compute <target> --wait
corerun notebooks create trainer --compute <target> --gpu 1 --profile <profile> -d <dataset> --wait
```

Useful flags: `--gpu N`, `--profile <profile>` (Kubernetes), `-d <dataset>`
(repeatable; mounts the dataset), `-e NAME=VALUE` and `--secret NAME=VALUE`
(repeatable; a secret is hidden after creation), `--workspace-env <id>` for a
variable the workspace already holds, `--image` to override the default, and
`--visibility shared` to open it to the whole workspace from the start.
Without `--wait` the command returns while the notebook is still starting;
`corerun notebooks wait <notebook>` picks it up from there.

A notebook is JupyterLab, and VS Code opens from it:
`corerun notebooks get <notebook>` prints each one's address. There is no type
to choose when creating one.

Each compute says which workspace roles may start notebooks on it (admins and
engineers unless changed). A refusal naming roles means this person's role is
not one of them there: try other compute from `corerun compute list`, or ask
whoever manages that compute; do not retry.

**A notebook holds its GPUs for as long as it runs, used or not.** Confirm the
compute and GPU count with the human before creating one with GPUs, check
`corerun notebooks list` for one of theirs that is merely stopped, and never
start one speculatively.

Never pass a real credential with `-e`: it is shown to everyone who can see the
notebook. Use `--secret`, or a workspace variable.

## Its datasets

```bash
corerun notebooks datasets <notebook>                    # what it mounts
corerun notebooks datasets <notebook> --add <dataset>    # mount one (repeatable)
corerun notebooks datasets <notebook> --remove <dataset> # unmount one
```

Datasets are mounted read-only at `/data/<name>`. On a host they change while
the notebook runs. On a Kubernetes cluster the notebook has to restart to
change them, and the command refuses unless given `--restart` -- which loses
the kernel and its variables, so ask the human first. A stopped notebook gets
them when it starts.

## Moving it, or giving it a GPU

A stopped notebook starts where it ran unless told otherwise:

```bash
corerun notebooks start <notebook> --compute <target>   # on other compute
corerun notebooks start <notebook> --gpu 1              # same compute, with its GPU
corerun notebooks start <notebook> --gpu 0              # same compute, GPU given back
```

Its files are the person's home, which follows it. The kernel and its
variables do not: moving is a restart. With GPUs the notebook runs the GPU
image, without them the CPU one; the platform picks the image that fits the
card. `corerun compute list` shows which compute has GPUs, and what they are.
Stop a running notebook first (`corerun notebooks stop`); ask the human
before taking a GPU, as for creating one.

## While it starts

`corerun notebooks get` shows what a start is doing (`Now: Pulling the
image: 45% of 8.4 GB` -- a first start on a host downloads the image, which
takes minutes), and any **warning** the notebook came up with anyway. The one
to act on: *"Your home could not be mounted …"*, with `Home: this compute's
own disk (not kept)`. The notebook works, but what is saved there does not
reach the workspace's storage and is lost with the compute -- tell the human,
and do not leave work there. The warning names the cause (the store refused
the host, or could not be reached).

## A stopped notebook's files

```bash
corerun notebooks files <notebook>                 # the workspace folder, as kept in storage
corerun notebooks files <notebook> experiments     # a folder within it
corerun notebooks cat <notebook> train.ipynb       # each cell's source, as last saved
```

Read-only, and only for a home kept in the workspace's storage: a home on a
host's own disk answers that it is not kept (exit code 1), and only the
running notebook can read it. `--json` gives the notebook's JSON as saved.

## Sharing

```bash
corerun notebooks share <notebook> someone@example.com       # one person
corerun notebooks share <notebook> someone@example.com --unshare
corerun notebooks visibility <notebook> shared               # everyone in the workspace
corerun notebooks visibility <notebook> personal             # takes it back from everyone
```

Going back to `personal` also withdraws access from anyone it was shared with
by name.

## Stopping and deleting

```bash
corerun notebooks stop <notebook>           # releases its compute; files survive
corerun notebooks start <notebook>          # where it ran; --compute / --gpu above
corerun notebooks delete <notebook> --yes   # the notebook and everything in it
```

Stop a notebook when the work is done: it is the difference between holding a
GPU and not. **Delete is not stop** — it removes what was in the notebook as
well, so confirm with the human first, and never delete or stop someone else's
notebook to free capacity. Without a terminal, delete fails with exit code 2
and removes nothing unless `--yes` is given.

## Inside a notebook

The `corerun` CLI and these skills are already installed there, signed in with
a credential the platform replaces every few hours — nothing needs a
`corerun login`. The same workspace's datasets, jobs, endpoints and registry
are one command away; submitting a job from a notebook is how to run training
that should outlive the session.
