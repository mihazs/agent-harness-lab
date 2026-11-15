"""Synthetic manifest, candidate specs and adapter bytes for the adapter staging command tests."""
import hashlib
import json
import os
from pathlib import Path

from agent_harness_lab.surfaces import PINS
from agent_harness_lab.workflow import identity

ADAPTER_ID = 'structured-error-v1'
ADAPTER = f'.ahl/adapters/{ADAPTER_ID}/adapter.py'
SPEC = f'.ahl/adapters/{ADAPTER_ID}/spec.json'
V1 = b'def present(envelope):\n    return {"original": envelope}\n'
V2 = b'def present(envelope):\n    return {"original": envelope, "v": 2}\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def spec_mapping(raw=V1, **changes):
    data = dict(adapter_id=ADAPTER_ID, version='1', content_digest=digest(raw), tools=['Read', 'Grep'],
                input_contract='tool-envelope-v1', output_contract='presented-envelope-v1',
                transformations=['identity', 'structured-error-v1'], side_effects='none', filesystem=[], network=[],
                credentials=[], nested_tools=[], limits=dict(max_input_bytes=4096, max_output_bytes=8192, timeout_ms=500),
                previous_digest=None, runtime='claude-code', runtime_version=PINS['claude-code'])
    data.update(changes)
    return data


def v2_mapping(**changes):
    return spec_mapping(V2, version='2', previous_digest=digest(V1), **changes)


def write_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    os.chmod(path, mode)
    return path


def manifest_mapping(root, **changes):
    """The manifest for the project at root; a keyword such as project= overrides a field."""
    data = dict(schema='ahl.adapter-authorization.v1', project=identity(Path(root).resolve()), version='1',
                tools=['Read', 'Grep'], adapters=[spec_mapping()])
    data.update(changes)
    return data


def write_manifest(folder, root, name='manifest.json', **changes):
    return write_json(Path(folder) / name, manifest_mapping(root, **changes))


def write_candidate(folder, mapping, raw, name='candidate'):
    """The candidate spec and its adapter bytes as two files, the way an optimizer hands them over."""
    folder = Path(folder)
    spec = write_json(folder / f'{name}.json', mapping)
    adapter = folder / f'{name}.py'
    adapter.write_bytes(raw)
    return spec, adapter
