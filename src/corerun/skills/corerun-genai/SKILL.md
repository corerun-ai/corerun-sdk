---
name: corerun-genai
description: Read and judge what an agent did on corerun — traces and their span trees, the conversations they group into, the judges that score them, review queues for human verdicts, issue detection over a sample of traces, and evaluation runs. Use when asked why an agent answered as it did, what it costs or how slow it is, whether its output is any good, or what is going wrong across many traces.
---

# corerun genai

Agent traces and everything built on them: what an agent was asked, every
step it took, what it cost, and what judges and people concluded about it.

## Everything hangs off an experiment

An experiment is where traces are collected, and almost every command needs
one. Start here, always:

```bash
corerun genai experiments            # id and name; the id is what the rest take
```

## Traces

```bash
corerun genai traces list -e <experiment> [--state ERROR] [--limit 50]
corerun genai traces get <trace-id> -e <experiment>    # the span tree, timed
corerun genai traces delete <trace-id> -e <experiment>
corerun genai traces assess <trace-id> correct no -e <experiment> -r "cited the wrong policy"
corerun genai sessions list -e <experiment>            # traces grouped by conversation
```

A short id works wherever a whole one does: the listing prints a prefix and
`get` resolves it, so an id copied off the screen can be pasted back. An
ambiguous prefix says so rather than picking.

`traces get` draws the span tree with a duration bar per span. Read it for
*where* the time went before asking why the whole thing was slow — a trace
that took thirty seconds usually spent twenty-nine of them in one span.

## Sending traces in

Nothing here does that, and nothing needs to be installed to do it. The
platform accepts OpenTelemetry on its own endpoint, so any instrumented
application already speaks it:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://<host>/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer $CORERUN_API_KEY"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_SERVICE_NAME=<the experiment's name>
```

Which experiment a trace lands in comes from `service.name`. An application
that already names itself needs only the endpoint and the key, and a name
nobody has used yet is created on the first export.

## Judges and review

```bash
corerun genai judges -e <experiment>                   # what scores these traces
corerun genai review queues -e <experiment> [-u <user>]
corerun genai review show <queue-id>                   # what is in it, and what was decided
corerun genai review new <name> -e <experiment>
corerun genai review add <queue-id> <trace-id>...
corerun genai review decide <queue-id> <trace-id> complete --by <who>
```

A judge is a model scoring a trace; a review queue is a person doing it. Use
the queue when the question is one a judge cannot settle.

Pass `--by` when completing one. It is not enforced: omit it and the verdict is
recorded against `unknown`, which reads as a real decision by someone nobody can
ask about afterwards. That is worse than an error, because nothing looks wrong.

## Evaluation runs

An evaluation run is an experiment's record of one scoring pass: an evaluate
from the SDK over a dataset, or issue detection over traces.

```bash
corerun genai evaluations -e <experiment>     # what they concluded
```

There is no command that starts a benchmark against a model: that ran as a
job on the workspace's compute and was withdrawn, to return as a hosted
service. Do not look for one or improvise it with `corerun jobs`.

One row per scoring pass, with a column per score it produced. The columns are
whatever the runs carry rather than a fixed set, so a blank means that run did
not measure that thing — not that it scored zero.

## Finding issues across many traces

Issue detection reads a sample of an experiment's traces with a model and files
what it finds as assessments on those traces, so the findings sit beside every
other score rather than in a report of their own.

```bash
corerun genai issues categories                  # the six: correctness, latency, execution, ...
corerun genai issues detect <experiment> --endpoint <endpoint> -c correctness,safety -n 10
corerun genai issues detect <experiment> --endpoint <endpoint> -t <trace-id> -t <trace-id> --wait
corerun genai issues show <job-id>               # how it is getting on, and what it found
```

`--endpoint` is the model endpoint that does the judging. Without `-c` every
category is read; `-t` names traces and overrides the `-n` sample (25 by
default). `--wait` stays until it finishes and exits non-zero if it failed;
without it, `detect` prints the job id to pass to `show`.

**It costs model calls: one per trace per category.** `detect` prints the
count before it starts — 25 traces across six categories is 150 calls on that
endpoint. Start with a small `-n` and one or two categories, and confirm with
the human before a large run or one on an endpoint other people depend on.

The findings are a model's opinion. Read the traces they point at before
repeating a finding to anyone as fact.

## From Python

```python
import corerun
corerun.init()

for trace in corerun.genai.traces(experiment="13", state="ERROR"):
    print(trace.trace_id, trace.duration_ms, trace.input_preview)

detail = corerun.genai.trace("tr-1c80…")
for span in detail.spans:
    print(span.name, span.duration_ms, span.span_type)

corerun.genai.assess(detail.trace_id, "helpfulness", 4, rationale="answered it")
```

`trace.failed` rather than comparing `state` to a string: the engine spells it
`ERROR`, and testing for `"FAILED"` matches nothing and looks like a healthy
trace.

## Two things that will catch you

**An experiment is required.** There is no such thing as the traces of a
workspace at large — the engine reads them out of one experiment — so a
command without `-e` is refused rather than answered broadly.

**A score is only as good as what it ran over.** `evaluations` shows the count
of examples beside the score for a reason: 1.000 over three examples is not a
result. Check the count before repeating the number to anyone.
