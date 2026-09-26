---
name: corerun-quota
description: Check what the workspace has room for before allocating anything on corerun — GPUs, jobs, notebooks, inference servers, storage. Use before submitting a job, starting a fine-tune, or deploying an endpoint, and whenever a creation fails with a limit or quota error.
---

# corerun quota

Creation is refused once a limit is reached. Check first — a refused job wastes
a round trip and an exhausted GPU pool wastes everyone's.

```bash
corerun quota show            # every limit, with usage and headroom
corerun quota gpus            # GPUs broken down by the workload holding them
corerun quota show --json     # for parsing
```

`show` flags anything already at its limit under **At limit:**. A limit of
`unlimited` has no ceiling — and no plan limits GPUs: they are the
organisation's own, so a GPU limit exists only if an administrator set one for
the workspace. Counts are of work that is running or starting; stopped and
finished work holds nothing.

When two views disagree — `quota show` and `quota gpus`, say — the more
specific one is the answer. Say out loud that they disagree rather than
reporting the summary as fact. And when the human tells you something about
their own workspace ("there is no limit"), check the detail view before
contradicting them.

Model calls are counted per organisation, per month, rather than per
workspace:

```bash
corerun org usage             # the plan, what is used against it, calls, tokens and spend per endpoint
```

On a plan that caps model calls, calls past the cap are refused until the month
turns; tokens are shown as a statistic, not limited. Spend is the
organisation's own prices on each endpoint's models (`corerun endpoints price`),
not a bill from the platform. It needs an organisation administrator.

## Before allocating

Run `corerun quota show` and compare against what you are about to ask for.
GPUs are the contended resource: a job asking for 4 when 2 are free will not
schedule, and nothing will tell you why until it sits queued.

If a resource is at its limit, say so and stop. Do not free room by stopping
someone else's work — jobs and endpoints belong to people, and a stopped
endpoint is an outage. Report what is full and let the human decide.

Limits are not self-service: an organisation administrator raises a
workspace's limits in the console (organisation settings, Workspace limits).
There is no CLI command for it — say who can, rather than looking for one.

## When creation fails

A `quota` or `limit` error means the ceiling, not a bug. Run `corerun quota show`,
report which resource is exhausted and what currently holds it
(`corerun quota gpus` for GPUs, `corerun jobs list` for jobs), and stop.
