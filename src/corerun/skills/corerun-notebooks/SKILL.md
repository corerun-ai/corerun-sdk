---
name: corerun-notebooks
description: Start, find, share, stop and delete notebooks on corerun — interactive sessions on a cluster or bare-metal host, with GPUs, mounted datasets and environment variables. Use when asked for a notebook or a place to work interactively, for a notebook's URL, to share one with a colleague, or to free the GPUs a notebook is holding.
---

# corerun notebooks

```bash
corerun notebooks list                      # add --status running
corerun notebooks get <notebook>            # status, compute, image, GPUs, datasets, URL
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

**A notebook holds its GPUs for as long as it runs, used or not.** Confirm the
compute and GPU count with the human before creating one with GPUs, check
`corerun notebooks list` for one of theirs that is merely stopped, and never
start one speculatively.

Never pass a real credential with `-e`: it is shown to everyone who can see the
notebook. Use `--secret`, or a workspace variable.

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
corerun notebooks start <notebook>
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
