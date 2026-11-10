"""Convention checks over one inventory node's parsed facts; each returns (status, reason) and reads no file.

A check sees only the flat facts the harness inventory parser already recorded for its kind, never raw text, so
it cannot execute, load or reread anything. It answers 'nonconformant' only for a fact the pinned public contract
(surfaces.CAPABILITIES) names as ignored, skipped, rejected or unknown by the runtime, 'unknown' when the parser
itself could not settle the file, and 'conformant' otherwise. Every answer is a convention check, never evidence
that the harness performs better or worse.
"""


def instruction(facts, runtime):
    # A plugin-root instruction file is never loaded by the runtime, so no size or emptiness rule applies to it.
    if facts.get('plugin_runtime_loading') == 'not_loaded_by_runtime': return 'unknown', 'instruction_not_loaded_by_runtime'
    if facts.get('empty') is True: return 'nonconformant', 'instruction_empty'
    if facts.get('exceeds_launch_skip_bytes') is True: return 'nonconformant', 'instruction_exceeds_launch_bytes'
    if facts.get('exceeds_default_project_doc_max_bytes') is True:
        return 'nonconformant', 'instruction_exceeds_project_doc_bytes'
    if facts.get('uncertain_import_tokens'): return 'unknown', 'instruction_imports_uncertain'
    if facts.get('instruction_files_setting') == 'instruction_files_setting_unknown':  # Claude reads it only if set
        return 'unknown', 'instruction_files_setting_unknown'
    return 'conformant', 'documented_format'


def path_rule(facts, runtime):
    return ('conformant', 'documented_format') if facts.get('rule_parse') == 'parsed' else ('unknown', 'rule_not_settled')


_HOOK_WARNINGS = ('plugin_hooks_wrapper_missing', 'unknown_event', 'unknown_field', 'unknown_handler_type')


def hook(facts, runtime):
    for name in _HOOK_WARNINGS:
        if facts.get('warning.' + name) is True: return 'nonconformant', 'hook_' + name
    handlers = facts.get('handlers') or 0
    if type(handlers) is not int: return 'unknown', 'hook_handlers_not_settled'
    if not handlers: return 'unknown', 'no_hooks_declared'  # absence does not show a convention is followed
    for index in range(handlers):
        if facts.get(f'handler.{index}.status') == 'parsed_but_skipped': return 'nonconformant', 'hook_handler_skipped'
        if facts.get(f'handler.{index}.on_failure') == 'invalid': return 'nonconformant', 'hook_on_failure_invalid'
    return 'conformant', 'documented_format'


def workflow(facts, runtime):
    return {'issues_found': ('nonconformant', 'workflow_lint_issues'),
            'no_issues_found': ('conformant', 'static_lint_clean')}.get(
        facts.get('lint_result'), ('unknown', 'workflow_lint_inconclusive'))


def agent(facts, runtime):
    # Only a project agents folder loads under the parser's disposition; a plugin layout loads a nameless file under
    # its filename and any other location is not loaded, so the disposition says nothing about the runtime there.
    if facts.get('layer') != 'project': return 'unknown', 'agent_layer_not_modelled'
    disposition = facts.get('disposition')
    if disposition == 'skipped_silently': return 'nonconformant', 'agent_skipped_by_runtime'
    if disposition == 'rejected_with_warning': return 'nonconformant', 'agent_rejected_by_runtime'
    if disposition != 'declared': return 'unknown', 'agent_disposition_unknown'
    if facts.get('unknown_field_count'): return 'nonconformant', 'agent_unknown_fields'
    if any(key.startswith('version_gate.') and str(value).endswith('not_satisfied') for key, value in facts.items()):
        return 'nonconformant', 'agent_field_above_version_pin'
    return 'conformant', 'documented_format'


def command(facts, runtime):
    if facts.get('frontmatter') == 'unsupported': return 'unknown', 'command_frontmatter_unsupported'
    if facts.get('name_key') is True: return 'nonconformant', 'command_name_key_ignored'
    if facts.get('paths_key') is True: return 'nonconformant', 'command_paths_key_ignored'
    return 'conformant', 'documented_format'


def skill(facts, runtime):
    """Agent Skills header, description and disclosure conventions, then whether the runtime's own root holds the file.

    One reason per skill, cheapest edit first. A violation is reported wherever the bundle sits; a skill with none is
    'conformant' only in the runtime's own skill root, with a settled scope, never when the file was not loaded."""
    if facts.get('frontmatter') == 'unsupported': return 'unknown', 'skill_frontmatter_unsupported'
    if facts.get('name_declared'):
        if not facts.get('name_valid'): return 'nonconformant', 'skill_name_invalid'
        if facts.get('name_reserved') and runtime == 'claude-code':  # documented for Claude skills only
             return 'nonconformant', 'skill_name_reserved_word'
        if not facts.get('name_matches_directory'): return 'nonconformant', 'skill_name_differs_from_directory'
    state = facts.get('description')
    if state == 'missing': return 'nonconformant', 'skill_description_missing'
    if state == 'not_text': return 'nonconformant', 'skill_description_not_text'
    if facts.get('description_chars', 0) > 1024: return 'nonconformant', 'skill_description_too_long'
    if facts.get('missing_references'): return 'nonconformant', 'skill_reference_missing'
    if not facts.get('description_trigger'): return 'nonconformant', 'skill_description_lacks_trigger_phrasing'
    if facts.get('body_lines', 0) > 500 and not facts.get('has_references'): return 'nonconformant', 'skill_body_without_references'
    if facts.get('load') == 'unsettled': return 'unknown', 'skill_load_scope_unsettled'
    if facts.get('load') != 'loaded': return 'unknown', 'skill_not_loaded_by_runtime'
    return 'conformant', 'documented_format'


def plugin(facts, runtime):
    form = facts.get('plugin_file')
    if form == 'manifest' and facts.get('name_declared') is not True: return 'nonconformant', 'plugin_manifest_name_missing'
    if form == 'marketplace' and facts.get('marketplace_entries') is None:
        return 'nonconformant', 'plugin_marketplace_entries_missing'
    if form in ('claude_settings', 'codex_config') and not (facts.get('plugins_declared') or facts.get('marketplaces_declared')):
        return 'unknown', 'no_plugins_declared'
    if form in ('manifest', 'marketplace', 'claude_settings', 'codex_config'): return 'conformant', 'documented_format'
    return 'unknown', 'plugin_file_not_settled'


def config(facts, runtime):
    """A configuration the allowlisted reader decoded conforms to its documented shape; values are never judged."""
    return 'conformant', 'documented_format'
