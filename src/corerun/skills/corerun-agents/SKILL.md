---
name: corerun-agents
description: Build, share and talk to agents on corerun — a coding-agent harness in a sandbox per person, with instructions, projects of files, connectors (MCP servers and Kubernetes clusters), custom MCP servers the platform runs (from API descriptions or an image), policies saying what it may do with them and network rules saying where its sandbox may connect. Use when asked to create or change an agent, give it tools or a cluster, decide what it may do (allow, ask, deny), let it reach a host (pip, npm, GitHub, an API), turn an API or an MCP server's image into a server agents can use, share it, ask it something from a terminal, or find out why it was refused.
---

# corerun agents

Agents live in GenAI workspaces. One is a harness (opencode, goose, ...) with
instructions, running in a sandbox of its own for each person who talks to it.
What it can reach is what it is given -- connectors -- and what it may do there
is what the policies allow.

```bash
corerun agents list                       # the agents you may use here
corerun agents mine                       # every workspace you belong to
corerun agents get <agent>                # harness, compute, model, tools, your sandbox
```

An agent is named by its name or id.

## Building one

Agents live in an agents workspace, and only there; make one first if there is
none (`corerun workspace create "Support agents" --kind agents --use`).

```bash
corerun agents harnesses <agent>          # what its compute can run
corerun agents create support-bot --compute <target> -d "Answers support questions" --instructions-file prompt.md
corerun agents update support-bot --instructions-file prompt.md
corerun agents delete support-bot --yes   # and everyone's conversations with it
```

`update` keeps whatever it is not given. Creating one needs the workspace's
permission to build agents; changing one, its creator or a workspace admin.

## Who may use it

```bash
corerun agents access <agent>                       # people, groups, invitations, everyone
corerun agents grant <agent> someone@example.com chat
corerun agents grant <agent> "Support Team" edit    # a group
corerun agents grant <agent> someone@example.com none
corerun agents everyone <agent> on                  # everyone in the workspace may chat
corerun agents invite <agent> someone@example.com   # not in the workspace yet
corerun agents invites <agent>
```

`chat` lets someone talk to it; `edit` lets them change it. Ask the human
before `edit` or `everyone on`: both reach people who were not asked.

## Publishing: answering with nobody signed in

```bash
corerun agents publish <agent>                         # as itself: a support or SRE agent
corerun agents publish <agent> --as me                 # as you, answering you alone
corerun agents publish <agent> --as someone@example.com  # as them, once they say so in the console
corerun agents publication <agent>
corerun agents unpublish <agent>
```

Published, it answers whoever reaches it through the organisation's channels
(the API, a website, a chat app). As itself it has its own account with only
what the agent is given, and its asks wait on its managers' Approvals page;
as a person it answers them alone, with what they have. Ask the human before
publishing: it opens the agent to people outside the console.

## Tools: connectors

A connector is an MCP server, or a Kubernetes cluster given as a kubeconfig.
The workspace keeps them; an agent is given some of them.

```bash
corerun connectors list
corerun connectors add github --url https://api.githubcopilot.com/mcp/ --auth oauth
corerun connectors add search --url https://mcp.example.com --auth bearer --token "$TOKEN"
corerun connectors add prod --kubeconfig prod.yaml --context prod-readonly
corerun connectors add wiki --url http://wiki.internal:8080/mcp --via gb10dgx01-host --auth bearer --token "$T"   # a server in your network
corerun connectors test prod
corerun connectors remove search --yes

corerun agents tools <agent>                        # what it has, and each tool's setting
corerun agents tools-set <agent> --connector github --connector prod \
    --tool-policy github:search_issues=allow --tool-policy github:create_issue=ask
corerun agents tools-set <agent> --clear
```

A system with an API but no MCP server becomes one the platform runs -- a
custom server, each API with its own credential, versioned (draft, test,
publish, roll back):

```bash
corerun mcp-servers create ERP --spec https://erp.example.com/openapi.json --reads --auth bearer --token "$T"
corerun mcp-servers call ERP list_invoices --args '{"status": "open"}'   # test the draft
corerun mcp-servers publish ERP                                          # now a connector agents can be given
corerun mcp-servers add-tools ERP --api erp --tools block_invoice        # the next version's draft
corerun mcp-servers rollback ERP 3                                        # an earlier version, published again
```

An MCP server that exists only as an image (most speak only stdio) is run by
the platform on one of the organisation's computes, and agents reach it
through the gateway like any other:

```bash
corerun mcp-servers catalogue                                             # GitHub, Postgres, and their fields
corerun mcp-servers run GitHub --image github --on <compute> --field token="$GH_TOKEN"
corerun mcp-servers run "Sales DB" --image postgres --on <compute> \
    --field host=db.internal --field database=sales --field user=ro --field password="$PGPASSWORD"
corerun mcp-servers run Wiki --image ghcr.io/acme/wiki-mcp:1.4 --on <compute> \
    --command "wiki-mcp --stdio" --secret WIKI_TOKEN="$T" --host wiki.acme.internal:443
corerun mcp-servers get GitHub        # its server's state: starting, running, or why it failed
corerun mcp-servers tools GitHub      # what the running server lists, each with its default
corerun mcp-servers choose GitHub --reads                      # the draft's tools; then call and publish
corerun mcp-servers secret GitHub GITHUB_PERSONAL_ACCESS_TOKEN="$NEW"   # replaced; the server restarts
```

- **It reaches only what it was given**: `--host` (HTTPS) and `--tcp` (a
  database) destinations, through the organisation's egress proxy, every
  connection recorded. Anything else is refused, and the organisation's and
  workspace's network rules still apply. A refused connection is in the audit
  trail: `corerun org audit --category agent_network --action mcp_server.network --since 24h`.
  A server that needs another host needs a new version (`run` again with the
  same name is refused -- change the draft in the console, or delete and run).
- **Secrets are written once and never shown again** -- `get` says only
  whether one is set. They are not part of a version: rolling back keeps
  today's values. Read them from an environment variable, never type them.
- **Tools are chosen from what the server lists**: read-only tools (by the
  server's own `readOnlyHint`) start allowed, everything else asked first. A
  tool the version does not have is refused at the gateway even if the server
  offers it. GitHub's server runs read-only unless `--field read_only=false`;
  Postgres's in restricted mode (no writes) unless `--field access_mode=unrestricted`.
  Start there, and widen only what the person asks for.
- **Versions pin the image digest.** A draft that changes only tools shares
  the running server; one that changes the image, its settings or its compute
  starts a server of its own, and publishing moves agents to it and stops the
  old one about a minute later. `rollback` starts the earlier image again and
  publishes it once it runs.
- **Where it can run**: a cluster or host whose operator offers MCP servers.
  `run` refusing one says so ("cannot run MCP servers from images: its
  operator needs updating, or it holds no agents to an egress proxy"): an
  operator from before this feature (its owner runs `sudo
  corerun-host-operator upgrade` on a host) or a cluster without the egress
  proxy (`egress.enabled` in its operator's chart). Report that to the person;
  it is not something to work round.
- A server that will not start says why in `get` (the image would not pull,
  the command exited, a secret has no value). Fix that and `secret`, or a new
  draft, starts it again; the platform also retries a published one every
  five minutes.

`tools-set` gives exactly the connectors named; anything left out is taken
away. Per-tool `allow`, `ask` or `off` is for MCP servers; what an agent may do
on a cluster is a policy (below). Adding a connector checks it live and is
refused if the check fails -- report the reason rather than retrying.

A connector with `--auth oauth` is signed in to by each person, and the agent
acts as them:

```bash
corerun connections list
corerun connections connect github        # opens the provider's sign-in page
corerun connections disconnect github
```

Kubeconfigs and tokens are secrets: read them from a file or an environment
variable, never paste them into a command line others can see, and never print
one back. Use a cluster credential that can do no more than the agent needs --
the policy decides per request, and the credential is the limit beyond it.

## What it may do: policies

Every request an agent makes through a cluster connector is decided by rules:
`allow`, `ask` (a person approves it in the chat, and the approval is
recorded), or `deny`. Rules come from three levels -- the organisation's, the
workspace's and the agent's own -- and the strictest that matches wins. A
request nothing allows is refused. An agent with no rules of its own may only
read (`get`, `list`, `watch`).

```yaml
rules:
  - name: read
    effect: allow
    match:
      action: [get, list, watch]
  - name: ask-before-writes
    effect: ask
    match:
      action: [create, update, patch, delete]
    except:
      - namespace: [scratch-*]
  - name: never-kube-system
    effect: deny
    match:
      namespace: [kube-system]
    message: kube-system is off limits
```

Match fields: `adapter`, `action`, `group`, `resource`, `subresource`,
`namespace`, `name`, `labels`, `flags`, `connector`, `agent`, each a list of
globs.

```bash
corerun agents policy <agent> > rules.yaml          # the agent's own rules
corerun agents policy-set <agent> -f rules.yaml
corerun agents policy-set <agent> --default         # back to read-only

corerun policies list                               # the workspace's and the organisation's
corerun policies get careful > careful.yaml
corerun policies create careful -f careful.yaml     # a workspace's: a workspace admin
corerun policies update careful --disable
corerun policies delete careful --yes

corerun policies try "kubectl delete deploy api -n prod" --agent <agent>       # what would be decided, nothing runs
corerun policies try "kubectl rollout restart deploy api -n prod" --agent <agent> -f rules.yaml   # with unsaved rules
```

Before saving a rule, and when an agent says it was refused, run
`policies try` with the command it tried: it names the level, policy and rule
that decided -- a refusal from the organisation's policy needs an organisation
administrator, not a looser agent rule.

Loosening is the human's decision: ask before turning an `ask` into `allow`, or
adding any write. A rule from a higher level cannot be loosened by a lower one,
so a refusal that names an organisation rule needs an organisation
administrator, not another agent rule.

## Where it may connect: network rules

An agent's sandbox reaches the network only through the platform's egress
proxy, which decides each connection by network rules: the same `allow`, `ask`
and `deny` at the same three levels, matching `host`, `port`, `action` and
`agent` (globs; `*.github.com` is every name under github.com, not
github.com itself, and not githubusercontent.com). What no rule matches gets
is chosen per level -- `deny` (an allowlist, and what applies when nobody
chose), `ask`, or `allow` (a blocklist) -- the strictest choice winning. The
platform's floor -- private, link-local and cloud metadata addresses -- is
never lifted, and the platform itself is always reached.

```yaml
rules:
  - name: Python packages
    effect: allow
    match:
      host: [pypi.org, files.pythonhosted.org]
      port: ["443"]
  - name: GitHub
    effect: allow
    match:
      host: [github.com, "*.github.com", "*.githubusercontent.com"]
      port: ["443"]
```

```bash
corerun agents network <agent>                      # where it was refused in the last seven days
corerun agents network-allow <agent> pypi.org       # one destination (port 443 unless host:port)
corerun agents policy <agent> --network > net.yaml  # its own network rules
corerun agents policy-set <agent> -f net.yaml --network
corerun agents update <agent> --enforce-network     # an older agent that only reports; not undone
corerun agents network-unmatched <agent> [deny|ask|allow|none]   # what no rule matches gets
corerun policies unmatched [deny|ask|allow|none] [--org]

corerun agents activity <agent> --since 7d          # every destination: decisions, bytes, rule, flags
corerun agents activity <agent> --timeline -c agent_tools -c agent_changes   # what it did, what was changed
corerun agents activity <agent> --timeline --csv activity.csv

corerun policies list --network                     # the workspace's and the organisation's
corerun policies create packages -f packages.yaml --network
```

Agents get round refusals: refused `files.pythonhosted.org`, one fetched the
same packages from `mirrors.aliyun.com`. agentguard flags that in the audit
trail as `agent.network.workaround` (`corerun org audit --action workaround`,
and "looks like a workaround" in `agents activity`):
a known mirror, relay, tunnel or paste host, or a host allowed only because
nothing matched right after a refusal. A deny list cannot keep up with
mirrors; to stop it, use Refuse and allow exactly what is needed.

When an agent says a download or a call failed, run `agents network` first:
a destination listed there was refused by the proxy (the conversation shows
the same as a **Blocked** card). An agent created before the network was
enforced only *reports* -- its refusals are marked "would be" and nothing is
refused -- until `--enforce-network`. Allowing a destination is the human's
decision: name the host and port and ask before adding it, and prefer a
narrow host to a wildcard. So is choosing `allow` for what no rule matches:
it turns the agent's rules into a blocklist -- ask first, and never loosen a
level an organisation administrator set.

On an Incus host the proxy runs beside the host operator; until it does, an
agent's sandbox is not started there ("this host's egress proxy for agents is
not running yet") -- that is the host's administrator's to look at
(`corerun-devops`), not a rule to change.

## Talking to it

```bash
corerun agents chat <agent> "What changed in the last deploy?"
corerun agents chat <agent> "And the one before?" --conversation <id>
corerun agents conversations <agent>
corerun agents conversation <agent> <id>
corerun agents conversation-delete <agent> <id>
```

`chat` answers what it can from the model at once. Work that needs the
sandbox -- running commands, using connectors -- is handed to it, and that part
continues in the console, which is also where `ask` approvals are given; the
command says so and prints where.

## Projects and files

Files given to an agent live in projects: `general` (yours, always there), any
you create, and `shared` (the agent's own, for every conversation; only those
who may edit the agent can write there).

```bash
corerun agents projects <agent>
corerun agents project-create <agent> "Q3 refunds"
corerun agents upload <agent> refund-policy.pdf --project "Q3 refunds"
corerun agents files <agent> --project "Q3 refunds"
corerun agents search <agent> "refund window"       # as the agent would find it
corerun agents download <agent> refund-policy.pdf --project "Q3 refunds"
corerun agents rm-file <agent> refund-policy.pdf --project "Q3 refunds"
corerun agents project-delete <agent> "Q3 refunds" --yes
```

## Allowed without asking

```bash
corerun agents consents <agent>                     # tools you said it may always use
corerun agents asks --waiting                       # what your agents are waiting for you to approve
corerun agents sandboxes                            # where the workspace's agents run
corerun agents sandbox-stop <id>                    # stop one
corerun agents consent-revoke <agent> <consent-id>  # make it ask again
```

## Do not

- Do not give an agent a cluster credential broader than its work, or a policy
  broader than the human asked for.
- Do not remove a connector or a policy other agents rely on without saying
  which agents use it (`corerun agents tools <agent>`).
- Do not share an agent with `everyone` or `edit` without asking.
- Do not publish an agent, or publish it as somebody, without the human
  asking: it then answers people outside the console.
- Do not print, log or paste a connector's token or kubeconfig, or a custom
  server's secret.
- Do not publish a custom server's version, roll one back or delete a server
  agents use without saying which agents are given it -- publishing changes
  their tools at once.
- Do not run an image you cannot name the publisher of, or turn a catalogue
  server's read-only or restricted mode off, without the person asking.
