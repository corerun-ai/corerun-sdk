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

## Which organisation

A person can belong to several organisations -- their own, and a partner
company's that invited them. A session is in one at a time:

```bash
corerun org list                 # * marks the current one
corerun org switch partner-co    # a new session there; then pick a workspace
```

Everything `corerun org ...` and `corerun ws ...` does after a switch is about
the organisation switched to. Switch back the same way.

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
first do, with that address. People from another company keep signing in
through their own company's directory: the invitation gives them a seat here,
and they reach it with `corerun org switch` or the console's organisation
switcher. An invitation expires; `invites` shows when.

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
corerun groups remove ml-research sara@acme.com   # loses what the group gave, everywhere
corerun groups revoke ml-research research        # the group's role in that workspace goes
corerun groups delete ml-research                 # every member loses its access; seats stay
```

With single sign-on, let the directory decide who is in a group: link the
identity provider's group, and everyone in it joins at their next sign-in and
leaves when they sign in no longer in it.

```bash
corerun groups link ml-research "ML Research"        # Okta, Google: the group's name
corerun groups link ml-research 0d6c7c1e-4b1a-...    # Entra: the group's object id
corerun groups links ml-research
corerun groups unlink ml-research "ML Research"      # the last link: its members leave now
```

The value is whatever the provider's token carries in `groups` (or `roles`),
matched without regard to case; Entra sends object ids unless its app is set
to send names. A membership the directory wrote says so (`show`, "From"):
removing it by hand is undone at the person's next sign-in -- change it in the
directory. People added by hand are never removed by the directory. The
Administrators group is not linked this way: it follows the provider's admin
group, or a directory group called `corerun-admin`.

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
`--domain` is the email domain it answers for (public domains such as gmail.com
are refused); `--admin-group` makes that group's members organisation
administrators; a Google Workspace provider also takes `--hosted-domain`.

**A new provider takes no sign-ins until a test sign-in passes.** `sso list`
shows it as "not live: test it". The test is a browser step, so hand it to the
human: in the console, Organization → SSO → **Test sign-in**. It shows what the
provider's token carries (address, directory, groups, roles). When the person
testing administers the directory (Global Administrator, the admin group, or a
`corerun-admin` group), the organisation also becomes that directory's home:
its people land here when they sign in ("live, directory home" in `sso list`).
Changing the issuer, client id or secret needs the test again.

**`--domain` restricts; only a verified domain routes.** Somebody who types an
address is sent to this organisation's sign-in only when its domain is
verified here -- by that directory-administrator test for Entra and Google
Workspace, or by DNS for anything else:

```bash
corerun org domains add acme.com        # prints the TXT record to publish
corerun org domains verify acme.com     # once the record is live
corerun org domains list
corerun org domains remove acme.com     # its addresses stop routing here
```

Publishing the record is the domain owner's step; hand the name and value to
the human. A domain is verified for one organisation at a time.

**Requiring the organisation's own sign-in.** Once a domain is verified and a
provider of the organisation's own is live, the organisation can close every
other way in for addresses there -- the Google, GitHub and Microsoft buttons,
passwords, sign-in codes, other organisations' providers:

```bash
corerun org sso require            # people at your verified domains
corerun org sso require --members  # strict: every member, whatever their address
corerun org sso require --off
```

It is refused until the provider is live and this session came through it
(sign in with it first), and it names everyone who would be locked out before
asking to confirm -- show that list to the human; never pass `--yes` for them.

**Secrets expire.** Record when, so administrators are reminded:
`corerun org sso update "Acme Entra" --client-secret <new> --secret-expires 2027-03-31`.
`sso list` shows a provider whose secret is being refused as `failing`. When
nobody can sign in because of it, an administrator uses "Recover access" on the
sign-in page: a one-time code by email, an hour's session, and every other
administrator is told.

It ends command-line sessions at those domains at once (browser sessions at
their expiry), so confirm with the human first and make sure the provider works
(a passing test sign-in) -- otherwise the domain's people are locked out.
Break-glass is exempt. Hosted edition only; a single-tenant install has nobody
else's buttons to close.

**A client secret expires.** When sign-in suddenly fails for everyone, the
secret is the first suspect:

```bash
corerun org sso update "Acme Entra" --client-secret <new secret>
```

```bash
corerun org sso remove "Acme Entra"
```

**Never remove or deactivate the only working provider** without another way
in -- you lock out every administrator, yourself included. `sso remove` is
refused (`last_way_in`) while people at its domain sign in only through it;
`--force` overrides that, and only on the human's say-so.

Nobody can remove themselves from the Administrators group, and its last member
cannot be removed; another administrator must. The same goes for a
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
corerun org service-accounts enable ci-deployer          # after the secret is rotated
corerun org service-accounts delete ci-deployer --yes
```

What one can reach, and taking a workspace away from it:

```bash
corerun org service-accounts workspaces ci-deployer
corerun org service-accounts revoke ci-deployer production
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
corerun org git remove github           # jobs naming it can no longer clone
```

Use a token that can **read** only the repositories needed, and say which in
`--note` for the next administrator. Tokens are stored encrypted and never
shown again. A workspace can also have connections of its own, from the
console.

## Connectors and policies for every workspace

What agents work with and what they may do with it can be set once for the
organisation. A connector added with `--org` is offered to every workspace; a
policy added with `--org` applies to every agent in every workspace.

```bash
corerun connectors add prod --kubeconfig prod.yaml --context prod-readonly --org
corerun connectors list --org
corerun policies create guardrails -f guardrails.yaml --org
corerun policies list --org
```

An organisation policy cannot be loosened below it: the strictest rule that
matches wins wherever it was set. Use it for the lines nobody crosses (no
deletes in `kube-system`, ask before any write in production), and leave the
rest to workspaces and agents. The `corerun-agents` skill has the rule format.

## Money and use

```bash
corerun org usage
corerun org prices list
corerun org prices set openai gpt-4o --input 2.0 --output 8.0
corerun org prices clear openai gpt-4o      # back to the list price, from now on
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

To answer "who did what" -- every change, every sign-in, and every request
refused (a token outside its scope, a workspace without the feature, a role
that does not reach) -- read the audit trail (plans with the audit log):

```bash
corerun org audit --since 7d                       # newest first
corerun org audit --actor ana@example.com --since 30d
corerun org audit --outcome denied --since 24h     # what was refused, and why
corerun org audit --action signin
```

The same events can go to the organisation's own SIEM as they happen:

```bash
corerun org audit add splunk --kind webhook \
  --address https://splunk.example.com:8088/services/collector/raw --token-env HEC_TOKEN
corerun org audit add qradar --kind syslog --address siem.example.com:6514 --format cef
corerun org audit exports
corerun org audit test splunk
corerun org audit disable splunk        # pause
corerun org audit enable splunk         # resume
corerun org audit remove splunk         # stop sending, and forget it
```

A destination is sent a test event before it is added, and refused if it does
not arrive. Pass the token through `--token-env`, never on the command line.

## Agent sandboxes

How long agents' sandboxes may sit unused, and how many ready sandboxes agents
keep, for the whole organisation:

```bash
corerun org agents show
corerun org agents set --idle-default 15 --idle-max 60
corerun org agents set --ready-per-agent 2 --ready-per-org 6
corerun org agents set --ready-per-org -1        # back to the installation's
```

Agent managers may only tighten these. Ready sandboxes hold memory on the
organisation's compute while they wait: ask before raising them.

## Do not

- Do not remove the last identity provider, or tighten network rules, without
  confirming another way in exists.
- Do not grant `admin` where `engineer` or `deployer` does the job.
- Do not reuse a person's token for automation; create a service account.
- Do not print, log or paste secrets -- a service account's secret, a provider's
  client secret, a git token, a connector's token or kubeconfig. They are shown once for a reason.
- Do not act on other people's workloads to free capacity; tell the human what
  is using it (`corerun quota gpus`).
