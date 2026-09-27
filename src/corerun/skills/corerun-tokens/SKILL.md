---
name: corerun-tokens
description: Issue, list and revoke corerun tokens for things that are not a person — an OpenTelemetry exporter sending traces, a CI job, a script — each limited to the scopes it needs. Use when something needs to call corerun without a person signed in, when setting up trace export, or when a token may have leaked.
---

# corerun tokens

A token is a credential for a machine: an exporter, a CI pipeline, a script. A
person at a terminal signs in with `corerun login` instead.

```bash
corerun tokens scopes                              # the common scopes, and what each admits
corerun tokens create "ci deploy" --scopes jobs:write --expires-in 30 -d "nightly retrain"
corerun tokens create "agent tracing" --scopes traces:write --otel
corerun tokens list                                # names, scopes, expiry, last used
corerun tokens revoke <id-or-name>
```

**Always pass `--scopes`.** Without it the token can do everything its creator
can, which is rarely what a machine needs and is the kind of default nobody
revisits. Scopes are `<resource>:<action>`, and `manage` implies `write`
implies `use` implies `read`, so issue the narrowest that works:
`traces:write` for an exporter, not `traces:read` as well unless something reads
them back.

`--expires-in` is in days (90 by default; `0` never expires — avoid it). `-d`
says what the token is for, for whoever reads the list later.

## The secret is shown once

`create` prints the token once and never again; the platform keeps only a hash.
Hand it straight to the human or the place it is going (a CI secret, an
environment variable). Do not write it into a file in the repository, a
notebook cell or a log. A token nobody wrote down is revoked and replaced, not
recovered.

`--otel` also prints the `OTEL_EXPORTER_OTLP_TRACES_*` exports an exporter
needs, with the token in them; set `OTEL_SERVICE_NAME` to the experiment the
traces should land in (see [corerun-genai](../corerun-genai/SKILL.md)).

## Revoking

`revoke` takes the id as `tokens list` shows it, or the token's name. Within
30 seconds every request carrying the token is refused, and anything still
using it stops working. Find out what uses it before revoking one that is not
known to have leaked — and revoke at once, without waiting, when it has.

Tokens that belong to the organisation rather than to a person — a pipeline
that should outlive whoever set it up — are service accounts, which an
administrator manages (see [corerun-admin](../corerun-admin/SKILL.md)).
