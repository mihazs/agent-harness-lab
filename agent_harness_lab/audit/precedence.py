"""Declared instruction ordering, never evidence that a runtime loaded or obeyed a file."""
from pathlib import PurePosixPath

from agent_harness_lab.audit import chain
from agent_harness_lab.sources.schema import digest

CODEX = 'https://developers.openai.com/codex/guides/agents-md'
CLAUDE = 'https://code.claude.com/docs/en/memory'


def report(inventory, task_cwd):
    runtime = inventory.runtime
    source = CODEX if runtime == 'codex' else CLAUDE
    files = [dict(path=n.path, root=n.root, state=n.declared_state, parse_status=n.parse_status)
             for n in inventory.nodes if n.kind == 'instruction']
    refusals = [dict(path=r.path, root=r.root, reason=r.reason, status='not_checked')
                for r in inventory.refusals if PurePosixPath(r.path).name in
                ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', 'CLAUDE.local.md')]
    relations = []
    # These are conditional selection/ordering rules, not observations or semantic conflict detection.
    if inventory.status == 'declared' and runtime == 'codex':
        cwd = task_cwd if task_cwd is not None else chain._recover(inventory)
        if cwd is not None and digest(cwd) == inventory.task_cwd_sha256:
            nodes = {n.path for n in inventory.nodes if n.origin == 'checkout' and n.kind == 'instruction'}
            refused = {r.path for r in inventory.refusals if r.root == 'checkout'}
            parts = PurePosixPath(cwd).parts if cwd != '.' else ()
            selected = []
            for depth in range(len(parts) + 1):
                scope = PurePosixPath(*parts[:depth])
                override, normal = (str(scope / name) for name in ('AGENTS.override.md', 'AGENTS.md'))
                if override in refused or normal in refused: break
                if override in nodes and normal in nodes:
                    relations.append(dict(higher=override, lower=normal, rule='same_directory_selection', source=source))
                candidate = override if override in nodes else normal if normal in nodes else None
                if candidate: selected.append(candidate)
            for index, lower in enumerate(selected):
                for higher in selected[index + 1:]:
                    relations.append(dict(higher=higher, lower=lower, rule='closer_directory_if_both_loaded', source=source))
    order = ((['user', 'project_root_to_task_directory'] if runtime == 'codex'
              else ['managed', 'user', 'project', 'local']) if inventory.status == 'declared' else [])
    return dict(runtime=runtime, status='declared' if inventory.status == 'declared' else 'not_checked',
        source=source, files=files, refusals=refusals, documented_load_order=order,
        order_source=source, conditional_relations=relations,
        assumptions=['default_project_root_and_fallback_names', 'files_loaded_within_budget'] if runtime == 'codex' else [],
        unchecked={name: 'not_checked' for name in
                   ('user', 'managed', 'runtime_configuration', 'imports', 'actual_loading', 'actual_compliance')},
        conflict_detection='not_checked', effective='unknown', publication_allowed=False)
