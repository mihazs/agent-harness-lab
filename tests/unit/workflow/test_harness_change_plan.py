"""Immutable review contracts never manufacture application authority."""
import dataclasses

import pytest

from agent_harness_lab.harness_change_plan import HarnessChangePlan, FileRevision, digest, REFERENCE_NAMES


class TestHarnessChangePlan:
    def test_exact_membership_modes_and_immutable_roundtrip(self):
        before={'AGENTS.md':('a'*64,0o644),'skills/old/SKILL.md':('b'*64,0o600)}
        after={'AGENTS.md':('a'*64,0o640),'skills/new/SKILL.md':('c'*64,0o600)}
        plan=HarnessChangePlan.create(before,after,references={name:'d'*64 for name in REFERENCE_NAMES},
            diagnosis={'status':'declared'},falsifiers=[],dependencies=[],controls=[],deferred=['hooks'])
        assert [o.action for o in plan.operations] == ['update','create','remove']
        assert HarnessChangePlan.parse(plan.document()) == plan
        assert plan.sha256 == digest(plan.document())
        assert not plan.document()['adoption_authority']
        with pytest.raises(dataclasses.FrozenInstanceError):plan.before=()
        with pytest.raises(ValueError):plan.validate_inventories(before,before)
        changed=plan.document();changed['before'][0]['mode']=0o777
        with pytest.raises(ValueError):HarnessChangePlan.parse(changed)

    def test_invalid_revision_and_unknown_surface_are_rejected(self):
        for path in ('../AGENTS.md','/AGENTS.md','x//AGENTS.md'):
            with pytest.raises(ValueError):FileRevision(path,'a'*64,0o644)
        with pytest.raises(ValueError):FileRevision('AGENTS.md','bad',0o644)
        with pytest.raises(ValueError):FileRevision('AGENTS.md','a'*64,True)

    def test_noop_and_mixed_executable_facets_remain_explicit(self):
        options=dict(references={name:None for name in REFERENCE_NAMES},diagnosis=[],falsifiers=[],dependencies=[],controls=[],deferred=['activation'])
        noop=HarnessChangePlan.create({}, {},**options)
        assert noop.document()['disposition'] == 'no_change'
        plan=HarnessChangePlan.create({}, {'AGENTS.md':('a'*64,0o644),'skills/example/scripts/run.py':('b'*64,0o644)},**options)
        assert plan.document()['unresolved_operations'] == ['skills/example/scripts/run.py']
        assert 'hook_contract_required' in plan.operations[1].facets
        detached=plan.document();detached['after'].clear()
        assert len(plan.after) == 2

    def test_agent_hook_workflow_and_policy_files_stay_unresolved_once_classified(self):
        # Agent files carry the hook facet; kinds no classifier row adopts never open the plan gate.
        options=dict(references={name:None for name in REFERENCE_NAMES},diagnosis=[],falsifiers=[],dependencies=[],controls=[],deferred=[])
        unresolved=('.claude/agents/reviewer.md','.claude/hooks/CLAUDE.md','.claude/workflows/x.js','.codex/agents/r.toml',
                    '.codex/rules/default.rules','.mcp.json','notes/x.txt')
        resolved=('.claude/CLAUDE.md','.claude/commands/frontend/component.md','.claude/commands/ship.md',
                  '.claude/rules/style.md','app/AGENTS.override.md','pkg/.claude/skills/demo/SKILL.md')
        plan=HarnessChangePlan.create({'.claude/commands/ship.md':('a'*64,0o644)},
            {path:('b'*64,0o644) for path in unresolved+resolved},**options)
        assert plan.unresolved_operations == tuple(sorted(unresolved))
        assert {o.path:o.surface for o in plan.operations if o.path in resolved} == dict(zip(resolved,('instruction','command','command','path_rule','instruction','skill')))
        assert HarnessChangePlan.create({},{'AGENTS.md':('a'*64,0o644)},**options).unresolved_operations == ()
        # An executable mode on a newly adoptable Markdown surface still needs the hook contract.
        executable=HarnessChangePlan.create({},{'.claude/rules/style.md':('a'*64,0o755)},**options)
        assert executable.unresolved_operations == ('.claude/rules/style.md',)

    def test_publication_rejects_contract_and_identity_even_with_forged_provenance(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:test',derived_from_private=False)
        for metadata in ({'profile':'ahl-harness-change-plan-v1'},{'plan_sha256':'a'*64},{'harness_change_plan':{}}):
            assert not public_export_allowed([dict(metadata,publication_allowed=True,provenance=provenance)])
