"""Private operator-supplied negative controls; review judgments remain conditional."""
from pathlib import Path
import tempfile

from agent_harness_lab.private_journal import encoded, record, write_bytes
from .integrity import artifacts, contained, copy_frozen, digest, tree
from .regressions import fields, references, sha256, text


def freeze(manifest_path, bank_path, author, target):
    manifest_path = Path(manifest_path).absolute()
    value, _ = record(manifest_path, 1024 * 1024)
    fields(value, 'schema_version curator evidence_refs tasks')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported held-out control manifest')
    text(value['curator']); references(value['evidence_refs'])
    if not value['evidence_refs'] or value['curator'].casefold() == author.casefold():
        raise ValueError('independent control curator and evidence required')
    bank, _ = record(bank_path, 1024 * 1024)
    tasks = {task['task_id']: task for task in bank['tasks']}
    if type(value['tasks']) is not dict or set(value['tasks']) != set(tasks):
        raise ValueError('held-out controls required for each task')
    if sum(len(items) if isinstance(items,list) else 101 for items in value['tasks'].values())>100:
        raise ValueError('held-out control count exceeds capacity')
    target.mkdir(mode=0o700)
    frozen = {}
    for index, (ident, task) in enumerate(tasks.items()):
        controls = value['tasks'][ident]
        if not isinstance(controls, list) or not 1 <= len(controls) <= 16:
            raise ValueError('bounded held-out controls required')
        frozen[ident] = []
        original = contained(Path(bank_path).parent, task['fixture'])
        for number, control in enumerate(controls):
            fields(control, 'path sha256'); sha256(control['sha256'])
            source = contained(manifest_path.parent, control['path'])
            bank_root = Path(bank_path).parent.absolute()
            if source.is_relative_to(bank_root) or bank_root.is_relative_to(source):
                raise ValueError('held-out controls must be separate from bank')
            name = f'{index}-{number}'
            copy_frozen(source, target/name, control['sha256'], max_files=100, max_bytes=1024*1024)
            tree(target,max_files=4096,max_bytes=64*1024*1024)
            from .bank_tools import materialize_sources
            with tempfile.TemporaryDirectory() as folder:
                baseline,probe=Path(folder)/'baseline',Path(folder)/'probe'
                copy_frozen(original,baseline,task['fixture_digest'])
                copy_frozen(target/name,probe,control['sha256'])
                materialize_sources(baseline);materialize_sources(probe)
                available = [p for p in task['artifacts'] if contained(probe, p).is_file()]
                original_available = [p for p in task['artifacts'] if contained(baseline, p).is_file()]
                if not available or artifacts(probe, available) == artifacts(baseline, original_available):
                    raise ValueError('held-out control must change exported artifacts')
            frozen[ident].append(dict(path=name, sha256=control['sha256'], artifacts=available))
    result = dict(schema_version=1, curator=value['curator'], evidence_refs=value['evidence_refs'], tasks=frozen)
    write_bytes(target/'controls.json', encoded(result))
    return result, digest(target)


def read(root, expected):
    root = Path(root)
    from .decision_receipts import private
    private(root,directory=True)
    if digest(root) != expected:
        raise ValueError('frozen held-out controls changed')
    return record(root/'controls.json', 1024 * 1024)[0]
