---
name: corerun-endpoints
description: Model endpoints on corerun — the stable address an application calls, its API keys, and publishing models behind it including ones corerun does not run. Use when asked for the URL to call a model, to rotate or find a key, to put a provider's model behind the platform's address, or when a client gets 401, 404 or the wrong model back.
---

# corerun model endpoints

An endpoint is the address; the models behind it come and go. That is the whole
point of the separation — the URL in somebody's application keeps working while
a model is redeployed, replaced, or moved to another machine.

**An endpoint serves many models at once.** Any number of deployments and
upstreams sit behind one address side by side, and each call picks one by the
`model` name it sends. Deploying behind an endpoint, or adding an upstream,
*adds* a model: nothing already there stops answering, and callers asking for
the existing names are unaffected. Only removing one (`remove-upstream`,
stopping or deleting a deployment) takes a model away. Do not warn the human
that publishing a model replaces another — it does not. What does matter is
the name: two members answering to the same name is ambiguous, so give each a
distinct `--served-name` / `--model`.

```bash
corerun endpoints list
corerun endpoints create <name> -d "Support bot"   # empty: address and key now, models later
corerun endpoints show <name>                # address, keys, what answers
corerun endpoints models <name>              # ask the endpoint itself
corerun endpoints call <name> "prompt" --stream
corerun endpoints metrics <name> --range 24h # every model behind it, one row each
```

`create` is for handing out an address before anything serves behind it; a
deployment with `--endpoint <name>` or `add-upstream` fills it later. A
deployment without `--endpoint` creates its own, so there is no need to create
one first.

## The address

```
https://<inference-host>/<endpoint-name>
```

Read it from `corerun endpoints show` rather than assembling it. The host is
deployment configuration and differs between environments — on the hosted
edition the path also carries the organisation — and a URL built by hand is one
that works until somebody moves it.

## Keys

Two live at once, deliberately:

```bash
corerun endpoints rotate-key <name> --key secondary
```

One key cannot be rotated safely — changing it and changing every caller are the
same instant, and the gap is an outage. Regenerate the idle one, move callers
onto it, then regenerate the other.

**Rotating a key that callers are using is an outage.** Ask which one is idle
before touching either. Do not print a key into a transcript, an issue, or a
commit; `show` masks them for that reason.

## Publishing a model

A deployment joins an endpoint when it is created:

```bash
corerun inference deploy --name <server> --model <id> --compute <target> \
  --endpoint <endpoint-name> --served-name <what-callers-ask-for>
```

A model somebody else runs is published the same way and answers on the same
address:

```bash
corerun endpoints add-upstream <name> --model chat \
  --base-url https://api.openai.com/v1 --api-key sk-... \
  --as gpt-4o --provider openai
corerun endpoints remove-upstream <name> chat
```

`--as` is what the provider is asked for when it differs from the
name callers use — that is what lets a caller keep asking for `chat` while the
model behind it changes. Changing that later is an edit, not a remove and add:

```bash
corerun endpoints edit-upstream <name> chat --upstream-name gpt-4.1
corerun endpoints edit-upstream <name> chat --api-key sk-new...   # rotate the provider key
corerun endpoints edit-upstream <name> chat --name chat-large      # rename what callers ask for
```

Anything not named is kept, the stored provider key included — it is never
shown back, so there is no need to know it to change something else.
**Renaming breaks every caller asking for the old name**; confirm first.

Calls to an upstream do not pass through the platform's own servers: on the
hosted edition the edge calls the provider directly, and the platform only
counts the call and times it. That timing is what `corerun endpoints metrics`
shows for an upstream — including the provider's own slowness, which is often
the answer to "why is this model slow".

## What it costs

A provider's model is priced from its **list price**, which the platform
keeps current from providers' published prices — nobody has to type one.
Add upstreams by provider so the list applies:

```bash
corerun prices providers                          # who, and where their API is
corerun prices search openai gpt-4o               # list prices per 1M tokens
corerun endpoints add-upstream <name> --model gpt-4o --provider openai --api-key sk-...
corerun endpoints show <name>                     # price column, and where it came from
```

The price in force is the first of: **this endpoint's own price**, **the
organisation's rate** for the provider's model, **the list price**. Set the
first two only where the list is not what is paid -- and only on a plan that
includes model pricing (not Free, which is refused with `plan_forbids`):

```bash
corerun org prices set openai gpt-4o --input 2.0 --output 8.0   # negotiated rate, every endpoint
corerun org prices list
corerun endpoints price <name> <model> --input 0.27 --output 1.1 --cache-read 0.07   # this endpoint only
corerun endpoints price <name> <model> --clear    # back to the organisation's or the list's
corerun endpoints metrics <name>                  # cost per model over the range
corerun org usage                                 # the month's spend, per endpoint
```

A model the platform runs has no list price; only an endpoint price prices it.

**A price applies from when it is set.** Each call is priced as it is
recorded, so changing a price never changes what past calls cost. Do not set
an organisation or endpoint price from memory: the list is usually right,
and a wrong override is a wrong spend report for everyone reading it — set
one only with a figure the human gives you. A cache price left out charges
cached tokens as input. A model name with a slash (`org/model`) works as
typed.

## Calling it

The endpoint speaks the OpenAI API, so any client already pointed at OpenAI
works by changing the base URL and key:

```python
client = OpenAI(base_url="https://<host>/<endpoint>/v1", api_key=key)
```

Models **corerun runs** also answer Anthropic's API on the same address, because
the engine serves both:

```python
client = Anthropic(base_url="https://<host>/<endpoint>", api_key=key)
```

Note the Anthropic base URL has no `/v1` — that SDK appends it. An endpoint that
only fronts external upstreams speaks whatever that provider speaks, so this
applies to deployments rather than to everything published.

## When a call fails

**401** — the key is wrong, or it belongs to a different endpoint. A key
identifies the endpoint, and the name in the URL has to agree with it.

**404 naming another address** — the endpoint moved. The body carries the URL
to use; the old one is no longer served.

**The wrong model answers** — check `corerun endpoints models <name>`. A request
naming no model, or a name the endpoint does not publish, does not route where
the caller assumed.

**413** — the request is larger than the endpoint accepts. Multimodal requests
carry base64 images and reach this sooner than people expect.

## Removing things

```bash
corerun endpoints delete <name>
```

**Deleting an endpoint breaks every application using its address**, and the
name cannot be reissued as the same URL to a different set of models without
that surprise. Refused while a deployment is still running behind it. Confirm
with the human first, and prefer removing an upstream to deleting the address.

## Performance

```bash
corerun endpoints benchmark <name> -m <model> --isl 4096 --osl 256 -c 1,4,16,64
corerun endpoints benchmark <name> -m <model> -p multi_turn --turns 6
corerun endpoints benchmark <name> -m <model> -p agentic --dataset claude-code
corerun endpoints benchmark <name> -m <model> -p sharegpt --compute <target>
corerun endpoints capacity <name> -m <model> --shape 4096/256   # how many fit at once
corerun endpoints benchmarks <name>                  # runs, and what the plan leaves
corerun endpoints benchmark-show <name> <run-id>     # every concurrency point
```

aiperf load tests against the endpoint's public address: time to first token,
inter-token latency, request latency and throughput at each concurrency. The
console's Performance tab charts them and overlays runs.

**The calls are real calls**: they count toward the plan's model calls, and an
upstream model's calls are billed by its provider. Say so before starting one,
and never benchmark an endpoint someone depends on without asking -- a load test
is load. One run at a time per person and per endpoint.

**Choose concurrency from capacity, not by guess.** `endpoints capacity` reads
what the model's engine reported at start — KV-cache tokens, the context it
serves — and says how many requests of each shape fit at once. Sweep from 1 to
a little past that number: below it the engine serves everything together, past
it requests queue, and the knee sits between. A shape longer than the context is
refused before the run starts.

Where it runs: on the platform's cluster by default, which the plan limits (runs
a month, minutes a run, concurrency); `--compute <target>` runs it on one of the
workspace's own clusters as a CPU container, with no benchmark limit.

