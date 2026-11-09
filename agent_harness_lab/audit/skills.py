"""Skill bundle context for the static audit: where each SKILL.md loads, its bundle files, and routing collisions.

Pure inventory facts, nothing is reread or executed. A SKILL.md is judged by checks.skill from the header facts
sources.skill_headers recorded plus the bundle facts computed here from the other inventory nodes: whether the
runtime's own skill root holds it, whether the bundle has a references/ folder and how many linked files are absent.
A file of a bundle that has a SKILL.md is part of that skill, not a surface of its own.

Collisions compare only skills that load together: the same discovery folder or one nested under a folder that
encloses the other (a skills folder in a sibling directory never loads with it), both in the audited runtime's root
and, for Codex, on the task directory chain. The rule is COLLISION_RULE: Jaccard similarity of the hashed
description token sets that TOKENIZER (sources.skill_headers) produced, at or above a threshold, with each side
holding at least MIN_TOKENS tokens. Skills whose descriptions were not read or not text never collide.
"""
from itertools import combinations
from pathlib import PurePosixPath
from agent_harness_lab.audit import chain
from agent_harness_lab.sources.skill_headers import TOKENIZER

COLLISION_RULE = 'ahl-skill-collision-jaccard-v1'
COLLISION_THRESHOLD = 0.5
MIN_TOKENS = 4
ROOTS = {'claude-code': ('.claude', 'skills'), 'codex': ('.agents', 'skills')}
SOURCE = 'https://agentskills.io/specification'
BEST_PRACTICES = 'https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices'
CODEX_SKILLS = 'https://developers.openai.com/codex/skills'


def _place(path, runtime):
    """(owner directory, own runtime root?) of a SKILL.md path, or None when it sits at no modelled depth."""
    parts = PurePosixPath(path).parts
    for index in range(len(parts) - 3):
        pair = parts[index:index + 2]
        if pair in ROOTS.values():
            deep = len(parts) == index + 4 if runtime == 'claude-code' else len(parts) >= index + 4
            return ('/'.join(parts[:index]) or '.', pair == ROOTS[runtime]) if deep else None
    if len(parts) == 3 and parts[0] == 'skills': return '.', False  # a project-owned root tree no runtime discovers
    return None


def _together(first, second):
    return first == second or first == '.' or second == '.' or first.startswith(second + '/') or second.startswith(first + '/')


class Context:
    def __init__(self, inventory, task_cwd=None):
        self.runtime = inventory.runtime
        self.cwd = task_cwd if task_cwd is not None else chain._recover(inventory)
        nodes = [n for n in inventory.nodes if n.origin == 'checkout']
        self.listed = {n.path for n in nodes} | {r.path for r in inventory.refusals if r.root == 'checkout'}
        self.skills = {n.path: n for n in nodes if n.kind == 'skill' and PurePosixPath(n.path).name == 'SKILL.md'}
        self.bundles = {str(PurePosixPath(path).parent) for path in self.skills}

    def supporting(self, node):
        """A present non-SKILL.md file inside a bundle that has a SKILL.md: part of that skill, not a surface."""
        parents = {str(parent) for parent in PurePosixPath(node.path).parents}
        return (node.origin == 'checkout' and node.kind == 'skill' and node.declared_state == 'present' and PurePosixPath(node.path).name != 'SKILL.md'
                and bool(parents & self.bundles))

    def load(self, path):
        place = _place(path, self.runtime)
        if place is None or not place[1]: return 'not_loaded_by_runtime'
        if self.runtime == 'claude-code': return 'loaded'
        if self.cwd is None: return 'unsettled'
        owner = place[0]
        return 'loaded' if owner == '.' or self.cwd == owner or self.cwd.startswith(owner + '/') else 'not_loaded_by_runtime'

    def facts(self, node):
        """Bundle facts for one SKILL.md node, added to its header facts."""
        bundle = str(PurePosixPath(node.path).parent)
        under = [path for path in self.listed if path.startswith(bundle + '/')]
        missing = 0
        for target in str(dict(node.facts).get('reference_links') or '').split():
            wanted = str(PurePosixPath(bundle) / target)
            missing += wanted not in self.listed and not any(path.startswith(wanted + '/') for path in under)
        return dict(load=self.load(node.path), has_references=any(path.startswith(bundle + '/references/') for path in under),
            missing_references=missing)

    def collisions(self, threshold=COLLISION_THRESHOLD):
        """{path: (related paths, measures)} for loaded skills whose description tokens overlap at the threshold."""
        if not 0 < threshold <= 1: raise ValueError('collision threshold must be in (0, 1]')
        sets = {}
        for path, node in self.skills.items():
            facts = dict(node.facts)
            words = frozenset(str(facts.get('description_tokens') or '').split())
            if (node.declared_state == 'present' and node.parse_status == 'parsed' and facts.get('description_tokenizer') == TOKENIZER
                    and len(words) >= MIN_TOKENS and self.load(path) == 'loaded'):
                sets[path] = words
        found = {}
        for first, second in combinations(sorted(sets), 2):
            if not _together(_place(first, self.runtime)[0], _place(second, self.runtime)[0]): continue
            shared, union = len(sets[first] & sets[second]), len(sets[first] | sets[second])
            if shared / union < threshold: continue
            measures = (('shared_tokens', shared), ('similarity_permille', shared * 1000 // union),
                        ('threshold_permille', round(threshold * 1000)), ('union_tokens', union))
            for path, other in ((first, second), (second, first)):
                related, rows = found.get(path, ((), measures))
                found[path] = (tuple(sorted(related + (other,))), max(rows, measures, key=lambda row: row[1][1]))  # strongest pair
        return found


# Public documentation behind each skill reason; the lexical and routing rules cite each runtime's own page.
CITATIONS = {
    'skill_name_invalid': SOURCE, 'skill_name_differs_from_directory': SOURCE, 'skill_description_missing': SOURCE,
    'skill_description_not_text': SOURCE, 'skill_description_too_long': SOURCE,
    'skill_name_reserved_word': BEST_PRACTICES, 'skill_reference_missing': SOURCE, 'skill_body_without_references': SOURCE,
}
ROUTING = ('skill_description_lacks_trigger_phrasing', 'skill_routing_collision')


def cite(reason, runtime):
    if reason in ROUTING: return CODEX_SKILLS if runtime == 'codex' else BEST_PRACTICES
    return CITATIONS.get(reason)
