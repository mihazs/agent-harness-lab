"""Bounded root instruction and configuration reads for private audit observations."""
import hashlib
import time


def read(project, inventory, task_cwd, name, folder_type, limit):
    if task_cwd != '.' or inventory.status != 'declared': return None
    source = {'codex': 'AGENTS.md', 'claude-code': 'CLAUDE.md'}.get(inventory.runtime)
    nodes = [node for node in inventory.nodes if node.origin == 'checkout' and node.kind == 'instruction' and node.path == source]
    if len(nodes) != 1 or nodes[0].declared_state != 'present' or nodes[0].parse_status != 'parsed': return None
    with folder_type(project, time.monotonic() + 30) as folder:
        instruction = folder.read(source, limit)
        configuration = folder.read(name, limit)
        folder.verify()
    if instruction is None or configuration is None or hashlib.sha256(instruction).hexdigest() != nodes[0].content_sha256:
        return None
    return instruction, configuration
