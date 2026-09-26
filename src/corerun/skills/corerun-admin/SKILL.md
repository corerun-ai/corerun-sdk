---
name: corerun-admin
description: Administer a corerun organisation from the CLI -- people and their workspace roles, invitations, groups, identity providers (SSO), service accounts for CI, the licence, per-workspace quotas, shared git connections, usage and security settings. Use when asked to onboard or offboard someone, change who can do what, connect Entra/Okta/Google sign-in, give a pipeline credentials, install a licence, cap a workspace's GPUs, or audit who has access.
---

# Administering an organisation

Everything here needs an **organisation administrator** and changes things
for everyone in the organisation. The platform refuses anyone else and says so.
Installing and upgrading the platform itself is the `corerun-devops` skill;
creating workspaces, storage accounts and connecting compute is
`corerun-workspaces`.

Start by knowing where you are:

```bash
corerun whoami
corerun org show
corerun org usage
```

## People

Everyone with a seat, and who administers:

```bash
corerun org members list
corerun org members invites
```

Access is **a role in a workspace**: `admin`, `engineer`, `deployer`,
`analyst`, `member`, `viewer`. `corerun groups roles` says what each permits.

```bash
corerun org members add sara@acme.com research --role engineer
corerun org members remove sara@acme.com research
corerun org members cancel-invite 3f2a91c0
```

`add` invites somebody who has never signed in; they get the role when they
first do, with that address. An invitation expires; `invites` shows when.

`remove` takes one workspace's role away. It does not take away their seat
or their roles elsewhere, and it does not stop what they already started --
their running notebooks and jobs keep running. **Offboarding** somebody is
removing them from every workspace (`org members list` shows how many they are
in) and, for an organisation that signs in through a directory, disabling
them in the directory, which is what actually stops them signing in.

## Groups

For more than a handful of people, grant roles to groups rather than people:
granting a group is one act however many are in it, and adding somebody to the
group gives them everything it holds.

```bash
corerun groups create "ML Research"
corerun groups grant ml-research research --role engineer
corerun groups add ml-research sara@acme.com
corerun groups show ml-research
```

A membership written by a directory sync says so (`show`, "From"); removing it
by hand is undone at the next sync -- change it in the directory.

## How people sign in

```bash
corerun org sso list
corerun org sso add "Acme Entra" --type entra \
  --issuer https://login.microsoftonline.com/<directory-id>/v2.0 \
  --client-id <application-id> --client-secret <secret> \
  --domain acme.com --admin-group <entra-group-object-id>
corerun org sso default "Acme Entra"
```

`--type` is `entra`, `google`, `okta`, `auth0`, `github`, `local` or `custom`.
The provider's application must allow `https://<console address>/auth/callback`
as a redirect URI. The issuer is checked before anything is saved.
`--domain` is the email domain it answers for; `--admin-group` makes that
group's members organisation administrators.

**A client secret expires.** When sign-in suddenly fails for everyone, the
secret is the first suspect:

```bash
corerun org sso update "Acme Entra" --client-secret <new secret>
```

**Never remove or deactivate the only working provider** without another way
in -- you lock out every administrator, yourself included. The same goes for a
network allow list in `corerun org security set` (see `corerun-workspaces`).

## Service accounts: credentials for machines

A CI pipeline, a scheduler or another service should never use a person's
token. A service account is a client id and secret exchanged for short-lived
tokens, holding a role in the workspaces it is granted and nothing else:

```bash
corerun org service-accounts create ci-deployer -d "GitHub Actions: production deploys"
corerun org service-accounts grant ci-deployer production --role deployer
corerun org service-accounts list
```

The secret is printed once, at creation. Hand it straight to the secret store
that will hold it; do not paste it anywhere else. A service account can never
be a workspace `admin`.

Rotate without downtime -- the old secret keeps working until you retire it:

```bash
corerun org service-accounts rotate ci-deployer
corerun org service-accounts retire-previous ci-deployer
```

Suspected leak: `disable` refuses its tokens at once, and nothing else changes.

```bash
corerun org service-accounts disable ci-deployer
corerun org service-accounts delete ci-deployer --yes
```

A person's own token for a script is `corerun tokens create`; prefer a service
account for anything that outlives the person.

## The licence (on-premises)

```bash
corerun org license show
corerun org license install ./license.json
```

`grace` is a trial or a lapsed licence still counting down (`until` says to
when); `restricted` refuses new work while everything running keeps running.
Installing takes effect at once, with no restart. On the hosted product there
is no licence and `show` says `not_required`.

## Limits per workspace

The plan sets the organisation's limits; a workspace can be given tighter ones
of its own so one team cannot take every GPU:

```bash
corerun org quota show research
corerun org quota set research --gpus 8 --notebooks 20
corerun org quota set research --gpus 0      # back to the plan's limit
```

Only the limits named change. Lowering a limit stops new work; it does not
stop what is running.

## Git hosts for training code

Jobs clone private code through a connection holding a read-only token. One
shared with the whole organisation:

```bash
corerun org git add github --token <fine-grained read-only token> --note "acme/trainer, acme/data-tools"
corerun org git test github acme/trainer
corerun org git list
```

Use a token that can **read** only the repositories needed, and say which in
`--note` for the next administrator. Tokens are stored encrypted and never
shown again. A workspace can also have connections of its own, from the
console.

## Money and use

```bash
corerun org usage
corerun org prices list
corerun org prices set openai gpt-4o --input 2.0 --output 8.0
```

`usage` is this month against the plan, with model calls, tokens and spend per
endpoint.

## Auditing access

To answer "who can do what here":

```bash
corerun org members list
corerun groups list
corerun groups show ml-research
corerun org service-accounts list
corerun tokens list
corerun org sso list
corerun org security show
```

A service account whose "Last used" is long ago, or never, is a candidate for
deletion -- confirm with its owner first.

## Do not

- Do not remove the last identity provider, or tighten network rules, without
  confirming another way in exists.
- Do not grant `admin` where `engineer` or `deployer` does the job.
- Do not reuse a person's token for automation; create a service account.
- Do not print, log or paste secrets -- a service account's secret, a provider's
  client secret, a git token. They are shown once for a reason.
- Do not act on other people's workloads to free capacity; tell the human what
  is using it (`corerun quota gpus`).
