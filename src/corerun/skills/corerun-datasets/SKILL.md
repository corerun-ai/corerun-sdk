---
name: corerun-datasets
description: Find, import, upload and inspect datasets on corerun — from HuggingFace, Kaggle, a URL, or local files and folders, with schema, format, size, tags and licence. Use when a task needs training data, when asked what data is available, to put local data on the platform, or before wiring a dataset into a job or fine-tune.
---

# corerun datasets

```bash
corerun datasets list
corerun datasets get <name>             # source, size, files, mount path
corerun datasets view <name>            # sample rows; --split, --page
corerun datasets files <name>           # every file, with its size
corerun datasets metadata <name>        # format, data type, tags, licence, access
```

## Importing

```bash
corerun datasets import huggingface <repo-id> --name <name>
corerun datasets import kaggle <dataset-id> --name <name>
corerun datasets import url <url> --name <name>
corerun datasets import-status <name>   # after --no-wait
```

An import downloads on the platform's side and is followed to the end unless
`--no-wait` is given.

## Uploading local data

```bash
corerun datasets upload ./reviews                       # a directory
corerun datasets upload train.jsonl --name support-chats  # a single file
corerun datasets upload images.zip --name product-images -d "Catalogue photos"
```

The platform accepts `.zip`, `.tar`, `.tar.gz` and `.tgz`: an archive is sent
as it is, and a directory or any other file is packed into a `.tar.gz` first.
It is extracted into the workspace's storage, tabular and image files are
converted to Parquet, and the command returns once all of that is done — the
answer already carries the size, file count and detected format. There is no
import to follow.

The name defaults to the path's last part without its archive suffix;
`--mount-path` defaults to `/data/<name>`. A name already taken is refused —
pick another rather than deleting the existing one.

Imports and uploads both consume workspace storage — check `corerun quota show`
first, and confirm with the human before bringing in anything sizeable.

## Recording what a dataset is

```bash
corerun datasets metadata <name> --tag nlp --tag en --license CC-BY-4.0
corerun datasets metadata <name> --access team          # private, team or public
corerun datasets metadata <name> --set owner=search-team --unset draft
```

`--tag` replaces the whole tag list (`--clear-tags` empties it); `--set` and
`--unset` change one recorded key and keep the rest. `--format` and
`--data-type` correct what was detected. Each option changes only its own field.

## Using one outside a job

```bash
corerun datasets download <name> --dest ./data   # skips files already there
```

## Before using one in a job

Check it exists and matches what the training code expects:
`corerun datasets view <name>` shows the real columns and rows. A job that fails
immediately on a data path or a column name is nearly always a mismatch here,
so read the schema rather than assuming the shape. Inside a job the dataset is
read-only at its mount path.
