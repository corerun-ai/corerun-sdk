---
name: corerun-workspaces
description: Create and delete corerun workspaces, choose which one the CLI acts in, and manage the organization's storage accounts and compute. Use when asked to set up a new workspace, switch between workspaces, add an S3 or ObjectIO account, or connect a cluster or bare-metal host.
---

# Workspaces, storage and compute

These are the administrative commands: they change what exists for everyone in
the organization, not what runs inside one workspace. Most need an
organization administrator.

## Signing in

```bash
corerun login --url https://<console>        # opens a browser
corerun login --url https://<console> --use-device-code   # a code to approve elsewhere, for a server
corerun auth whoami                          # address, workspace, whether it connects
corerun auth status                          # the same answer
corerun logout                               # same as `corerun auth logout`
corerun version
```

`corerun login` and `corerun auth login` are one command, as are `logout` and
`auth logout`. Logout removes the token and keeps the recorded address, so the
next login goes back to the same deployment. `--token` signs in with a token
already in hand (a scoped one from `corerun tokens create`) instead of a
browser; `-w` records the default workspace at the same time. The skills
themselves: `corerun skills list`, and `corerun skills install --force` after
upgrading the CLI.

## Which workspace you are in

Every resource command acts in one workspace, sent as a header. Check before
doing anything that allocates.

```bash
corerun workspace show          # which one commands act in
corerun workspace list          # the ones you belong to
corerun workspace set speech    # change it
```

`corerun ws` is the shorter spelling of all of these.

## Creating a workspace

```bash
corerun workspace create "ML Research"
corerun ws create "Speech" --slug speech --capabilities notebooks,training
corerun ws create "Scratch" --use
```

The slug is derived from the name when omitted. `--use` makes it the workspace
this CLI acts in, which saves a `set` afterwards.

`--capabilities` is a subset of `notebooks, training, models,
datasets, images, endpoints, experiments, traces`; omitting it means all of them. It decides what
the console shows and what the workspace is for — a serving-only workspace
asking for `endpoints` is clearer than one that offers training nobody will
use. It is not an authorization boundary: do not reach for it to stop somebody
doing something.

Storage comes from the organization's shared account when there is one. The
workspace gets a bucket of its own — `cr-<slug>-<hex>` — and on ObjectIO a
credential confined to that bucket. Nothing to pass; it happens on creation.
A workspace created before the organization had a shared account gets its
bucket the moment one is added: registering, replacing or removing the
account re-provisions every workspace under it.

## Changing a workspace

```bash
corerun workspace edit speech --name "Speech Research"
corerun ws edit speech --kind deploy
corerun ws edit speech --capabilities notebooks,training,models
```

`--kind` is `genai`, `ml` or `deploy`: which rail and home page the console
opens on. It never adds or removes anything the workspace can do.

`--capabilities` is the whole set, not an addition — anything left out is
withdrawn at once and its routes answer 404 (the data stays; granting it back
brings it into view). **Withdrawing one people are using takes their work out
of sight mid-task**; confirm with the human first.

## The organisation's security settings

```bash
corerun org security show
corerun org security set --access-ttl 30m --lockout-attempts 5
corerun org security set --allow-network 10.0.0.0/8 --deny-country KP
corerun org security set --clear
```

An organisation administrator can make signing in stricter than the
installation's defaults: shorter token and session lifetimes, API keys that
must expire sooner, a tighter lockout, longer passwords, and the networks and
countries its people may come from. Anything looser than the installation
allows is refused, naming the field. `set` changes only the settings named.

**A network or country rule applies to you too.** An allow list that does not
include where you are signing in from locks you and every other administrator
out at the next refresh. Check `show` first, confirm the list with the human,
and never set an allow list on someone's behalf without it.

## Deleting a workspace

```bash
corerun workspace delete scratch
corerun ws delete scratch --yes
```

**This deletes everything in it** — notebooks, jobs, endpoints, registered
models, datasets. Name, slug or ID all resolve. Confirm with the human first
and say what the workspace contains; `--yes` exists for scripts, not for
skipping the question on someone's behalf.

Note it does not currently deprovision the workspace's object-store bucket or
its scoped credential — those outlive the workspace and need removing by hand.

## Storage accounts

Organization-wide by default, which is almost always what is wanted: every
workspace created afterwards draws a bucket from it.

```bash
corerun storage list
corerun storage add orgs3 --endpoint https://s3.example.com \
    --access-key AKIA... --secret-key ...
corerun storage update orgs3 --plane git
corerun storage delete orgs3
```

`--provider` is one of `objectio, minio, ceph, aws, other` (default
`objectio`). Only ObjectIO can mint a credential confined to a single bucket;
on everything else each workspace uses the account key, so the bucket is a
convention rather than a boundary — say so rather than implying isolation.

`--plane` decides where model weights live: `git` (default) puts them in the
platform's git service with LFS, `s3` puts them in the bucket.

`-w/--workspace` scopes an account to the current workspace instead of the
organization. Reach for it only when one workspace genuinely needs its own
account; the org-wide one is what makes new workspaces work without setup.

**Changing or removing an account does not move anything.** Datasets,
checkpoints and model weights stay in the old account and stop being readable.
Treat `update` of an endpoint or key, and `delete`, as breaking, and say so
before running either.

## Compute

```bash
corerun clusters list
corerun clusters get <name>
corerun clusters profiles <name>       # the shapes people can launch
corerun clusters types                 # what a cluster can be added as, and its defaults
corerun clusters add <name>            # prints the manifest to apply
corerun clusters manifest <name>       # that manifest again, as it stands now
corerun clusters rm <name>
corerun hosts add <name>               # bare metal; prints an installer
corerun hosts rm <name>
corerun clusters scope <name> --organization          # share with every workspace
corerun clusters scope <name> --workspace-only <ws>   # give to one workspace
```

`clusters list` returns both the organization's shared clusters and this
workspace's own, each tagged with its scope. A cluster added at organization
scope is shared into every workspace; one added at workspace scope is not.
Check the scope before removing anything — removing a shared cluster takes it
away from workspaces you are not looking at.

Scope can be changed later with `clusters scope`, by an organization
administrator, without reinstalling anything on the machine. Giving a shared
cluster to one workspace is refused while other workspaces have work running on
it; say which, rather than stopping their work to make it pass. A host's
architecture is detected when it connects — `hosts add` does not need `--arch`.

`corerun compute list` shows the targets work can name with `--compute`;
`corerun compute get <name>` one of them in full, and `corerun compute types`
the kinds this platform supports.

`clusters add` and `hosts add` do not reach out to the machine. They hand back
a manifest or an installer for somebody to run there, and the operator joins
from its side. Nothing runs until it does.

### An operator's token

```bash
corerun clusters token rotate <name>   # new token; the connected operator drops off
corerun clusters token revoke <name>   # clear it; the operator can no longer connect
```

**Rotating disconnects the cluster until it is given the new token** — it does
not come back on its own. Re-fetch `corerun clusters manifest <name>` and apply
it on the cluster (or re-run a host's connect command). Revoking clears only
the token the cluster holds now, not a replacement already handed out, so it is
not a way to cut off an operator you have lost track of. Confirm either with the
human: work running there becomes unreachable.
