---
name: corerun-finetune
description: Run LoRA, QLoRA and full fine-tuning jobs on corerun — size one against the compute before it runs, pick a base model and dataset, register the result in the model registry, track it to completion. Use when asked to fine-tune, adapt, or specialise a model on a dataset.
---

# corerun fine-tuning

```bash
corerun finetune list                    # add --status running
corerun finetune create --name <name> \
    --model <hf-id or registry://name> --dataset <dataset name> \
    --compute <target> --profile <profile> --method lora \
    --eval-split 0.1 --as <result-model-name>
corerun finetune wait <job-id>
corerun finetune logs <job-id> --follow    # loss as it is logged
corerun finetune stop <job-id>             # keeps the record; delete removes it
```

Methods: `lora` (adapters on a bf16 base), `qlora` (a 4-bit base: fits a model
on a smaller GPU, slower), `full` (all weights: needs several times the memory).
Tunables include epochs, batch size, learning rate, LoRA rank and alpha, and max
sequence length. `--as` registers the result as the next version of that model
when the job succeeds; `--eval-split` holds a share of the data out for
evaluation. `--priority low|normal|high` and `--max-runtime <minutes>` are
optional.

## Before creating

Size it first. `plan` creates nothing: it says how big the model is, how much
memory each method needs per GPU, which of the compute's profiles each method
fits on, and which method and profile to start with.

```bash
corerun finetune plan <hf-id or registry://name> --compute <target>
corerun finetune plan <model> --compute <target> --batch-size 8 --max-seq-length 4096
```

Take its recommendation unless the human has a reason not to — a method that
does not fit is out-of-memory an hour in, not a slow run. The figures are
estimates; a `-` for every method on every profile means nothing there holds
it, and QLoRA or a bigger profile is the conversation to have. A gated model
with no token on file is flagged here too.

Three things fail a fine-tune before it starts, so check them:

```bash
corerun quota show                       # GPUs available
corerun datasets get <dataset>           # columns: messages, instruction/output, or text
corerun compute list                     # target exists and has the right GPUs
```

A gated Hugging Face model downloads with the workspace's Hugging Face token;
without one the job fails at the download.

**A fine-tune holds GPUs for hours.** Confirm base model, dataset, method and
GPU with the human before creating one, and check `corerun finetune list` for an
equivalent run already going — duplicated fine-tunes are the most expensive
mistake available here.

## Afterwards

Loss, evaluation loss and learning rate are logged to the job's run as it
trains: `corerun runs list finetune --order-by metrics.eval_loss` compares
fine-tunes (the experiment is `finetune` unless one was named), and
`corerun runs metric <run-id> eval_loss` reads a curve.


A LoRA or QLoRA fine-tune produces an adapter that
[corerun-inference](../corerun-inference/SKILL.md) can load onto its base model;
with `--as` it is already in the registry, which
[corerun-models](../corerun-models/SKILL.md) can stage.
