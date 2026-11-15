# Stage a presentation adapter and roll it back

Use this when a project has decided to try one presentation adapter, a small
Python module that adds metadata to a tool result without changing it, and you
want a candidate version deployed through a journal you can undo. Everything here
is offline. AHL never runs the adapter, installs a hook or contacts a runtime, and
a review shows only that a candidate stays inside the authority you declared. It
does not show that the adapter helps. For the contract and its limits, read
[controlled experiments](controlled-experiments.md); for why no runtime is
supported yet, read the [decision record](adapter-runtime-decision.md).

## Before you start

You need the project directory, a writable private folder outside it for the
manifest, and the candidate as two files: a JSON spec (`ahl.adapter-spec.v1`) and
the adapter bytes whose SHA-256 the spec declares. Both can come from anyone,
including an optimizer. Neither can grant anything the manifest does not.

## 1. Write the manifest outside the project

The manifest is yours alone. Create it in a folder the project does not contain and
that nobody else can write:

```json
{
  "schema": "ahl.adapter-authorization.v1",
  "project": "<project identity>",
  "version": "1",
  "tools": ["Read", "Grep"],
  "adapters": [ { "adapter_id": "structured-error-v1", "...": "the authorized AdapterSpec" } ]
}
```

`project` is the identity AHL derives from the resolved project path; a manifest
for another path is refused. Each entry of `adapters` is the full spec you
authorize for that adapter. A candidate may keep or narrow it but never widen its
tools, transforms, filesystem, network, credential or nested-tool grants, or its
limits. Run `chmod 600` on the file. AHL refuses a manifest that is inside the
project, writable by group or others, a symlink, or has an unknown or missing
field.

## 2. Review a candidate

```sh
ahl adapters stage . --manifest ~/private/adapters.json \
  --candidate spec.json --adapter-bytes adapter.py
```

This writes nothing. Exit 0 prints `awaiting_review` with the
`candidate_spec_sha256` and the command that deploys it. Exit 3 prints `blocked`
and the reasons, for example `expanded_network` or `adapter_not_authorized`; fix
the candidate or, if you really intend the wider authority, change the manifest
yourself and review again. Exit 2 means the input is invalid: bytes that differ
from the declared digest, a candidate that does not follow the deployed version, or
a manifest problem.

The first deployment of an adapter has no `previous_digest`. A later version must
name the digest of the bytes now deployed as its `previous_digest`, and the
manifest's authorized spec must be the version it follows. After you accept a
version, update the manifest entry to it before reviewing the next one.

## 3. Deploy the reviewed candidate

Repeat the command with the digest it printed:

```sh
ahl adapters stage . --manifest ~/private/adapters.json \
  --candidate spec.json --adapter-bytes adapter.py --authorize-spec SHA256
```

AHL writes `.ahl/adapters/<adapter_id>/adapter.py` and `spec.json` without an
execute bit, through a private journal under its state directory, and prints
`deployed` with a deployment ID. A digest that names another candidate exits 2 and
writes nothing. If the write fails or is interrupted, both files return to their
previous state, or the receipt names what remains.

## 4. Roll back

Preview first; it changes nothing:

```sh
ahl adapters rollback . --manifest ~/private/adapters.json --deployment ID
ahl adapters rollback . --manifest ~/private/adapters.json --deployment ID --execute
```

`--execute` restores the exact previous bytes and modes of both files, and removes
them when the deployment was the first. Running it again is a no-op. A later edit
to either file, a newer deployment, or a manifest that no longer authorizes the
version it would restore is a `conflict` (exit 3), and your files stay as they
are.

## What this does not do

It installs no hook, calls no runtime, deploys nothing automatically and measures
no benefit. The runtime capability stays `unsupported` in every packet. Whether an
adapter improves tool recovery needs a matched identity, prompt-only and adapter
evaluation with independent grading, which is still open.
