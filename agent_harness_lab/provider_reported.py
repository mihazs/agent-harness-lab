"""Provider-reported figures of an owned model call (#823): the runtime's own numbers, copied and labelled unverified.

AHL does not measure money. A figure the runtime's closing record does not carry is absent, never zero; a malformed one is
dropped; two closing records (or none) leave the call without a block. Nothing here is summed, compared or used for a decision.
"""
import json
import math

PROFILE = 'ahl-provider-reported-v1'
SOURCE = 'provider_reported_unverified'
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
                'cached_input_tokens', 'reasoning_output_tokens')  # integer usage totals either runtime names; other usage keys are not copied
CLOSING = {'claude': 'result', 'codex': 'turn.completed'}  # the transcript event that closes a call, per runtime


def _count(value):
    return type(value) is int and value >= 0


def _amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def block(stdout, agent):
    """The block for the one closing record of a runtime transcript, or None when it reports nothing usable."""
    kind = CLOSING.get(agent)
    records = []
    for text in str(stdout).split('\n'):
        try: event = json.loads(text)
        except (ValueError, RecursionError): continue  # a line that is not a record is not evidence of a figure
        if isinstance(event, dict) and event.get('type') == kind: records.append(event)
    if len(records) != 1: return None
    record, found = records[0], {}
    for name, key, valid in (('duration_ms', 'duration_ms', _amount), ('num_turns', 'num_turns', _count),
                             ('reported_cost', 'total_cost_usd', _amount)):
        if agent == 'claude' and valid(record.get(key)): found[name] = record[key]  # Codex closes a call with token usage only
    if 'reported_cost' in found: found['reported_cost_unit'] = 'usd'
    usage = record.get('usage')
    totals = {name: usage[name] for name in TOKEN_FIELDS if isinstance(usage, dict) and _count(usage.get(name))}
    if totals: found['usage'] = totals
    if not found: return None
    return dict(profile=PROFILE, source=SOURCE, controller_verified=False, certification_authority=False, **found)


def record(calls, stage, attempt, stdout, agent):
    """Note one owned model call in ``calls``; its ``provider_reported`` block is left out when the transcript carries none."""
    found = block(stdout, agent)
    calls.append(dict(stage=stage, attempt=attempt, **(dict(provider_reported=found) if found else {})))


def line(calls):
    """The one terminal line naming each call's reported cost, or None when no call reported one. No total is computed:
    a sum over calls that did not all report would read as a complete cost."""
    if not isinstance(calls, list): return None
    costs = [(call, (call.get('provider_reported') or {}).get('reported_cost')) for call in calls if isinstance(call, dict)]
    if not any(cost is not None for _, cost in costs): return None
    shown = ', '.join(f"{call['stage']}-{call['attempt']}=" + (f'{cost} usd' if cost is not None else 'unreported') for call, cost in costs)
    return f'ahl: provider-reported cost: {shown} (unverified)'
