---
name: corerun-agents
description: Build, share and talk to agents on corerun — a coding-agent harness in a sandbox per person, with instructions, projects of files, connectors (MCP servers and Kubernetes clusters) and policies saying what it may do with them. Use when asked to create or change an agent, give it tools or a cluster, decide what it may do (allow, ask, deny), share it, ask it something from a terminal, or find out why it was refused.
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

## Tools: connectors

A connector is an MCP server, or a Kubernetes cluster given as a kubeconfig.
The workspace keeps them; an agent is given some of them.

```bash
corerun connectors list
corerun connectors add github --url https://api.githubcopilot.com/mcp/ --auth oauth
corerun connectors add search --url https://mcp.example.com --auth bearer --token "$TOKEN"
corerun connectors add prod --kubeconfig prod.yaml --context prod-readonly
corerun connectors test prod
corerun connectors remove search --yes

corerun agents tools <agent>                        # what it has, and each tool's setting
corerun agents tools-set <agent> --connector github --connector prod \
    --tool-policy github:search_issues=allow --tool-policy github:create_issue=ask
corerun agents tools-set <agent> --clear
```

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
corerun agents consent-revoke <agent> <consent-id>  # make it ask again
```

## Do not

- Do not give an agent a cluster credential broader than its work, or a policy
  broader than the human asked for.
- Do not remove a connector or a policy other agents rely on without saying
  which agents use it (`corerun agents tools <agent>`).
- Do not share an agent with `everyone` or `edit` without asking.
- Do not print, log or paste a connector's token or kubeconfig.
