"""Synthetic ATIF-v1.8 trajectories for the importer tests; no real session content."""
import copy
import json

CANARY = 'CANARY-7d1f-private-token'


def call(call_id, name='read_file', **arguments):
    return dict(tool_call_id=call_id, function_name=name, arguments=arguments or dict(path='a.txt'))


def agent_step(step_id, calls=(), results=None, **extra):
    """An agent step whose observation answers every call unless `results` says otherwise."""
    step = dict(step_id=step_id, source='agent', message='working', model_name='model-x')
    if calls:
        step['tool_calls'] = list(calls)
        answers = [dict(source_call_id=c['tool_call_id'], content='output') for c in calls] if results is None else results
        step['observation'] = dict(results=answers)
    elif results is not None:
        step['observation'] = dict(results=results)
    step.update(extra)
    return step


def trajectory(steps=None, **root):
    document = dict(schema_version='ATIF-v1.8', agent=dict(name='agent-x', version='1.0.0', model_name='model-x'),
        steps=steps if steps is not None else [dict(step_id=1, source='user', message='do the task'), agent_step(2, [call('c1')])])
    document.update(root)
    return document


def full():
    """One document that carries every declared field of the mapping at least once."""
    ref = dict(trajectory_id='sub-1', trajectory_path='sub.json', session_id='s-sub', extra=dict(k=1))
    child = trajectory([dict(step_id=1, source='user', message='child task')], trajectory_id='sub-1', session_id='s-sub')
    parent_steps = [
        dict(step_id=1, source='system', message='rules', extra=dict(k=1), llm_call_count=0, is_copied_context=False,
             timestamp='2025-10-16T14:30:00Z'),
        agent_step(2, [dict(call('c1'), extra=dict(k=1))], [dict(source_call_id='c1', content='out', extra=dict(k=1),
                subagent_trajectory_ref=[ref])], reasoning_content='thinking', reasoning_effort='low', llm_call_count=1, extra=dict(k=1),
            metrics=dict(prompt_tokens=100, completion_tokens=20, cached_tokens=40, cost_usd=0.01, prompt_token_ids=[1], completion_token_ids=[2],
                         logprobs=[-0.1], extra=dict(k=1))),
    ]
    return trajectory(parent_steps, session_id='s-root', trajectory_id='root-1', notes='n', continued_trajectory_ref='next.json', extra=dict(k=1),
        subagent_trajectories=[child], final_metrics=dict(total_prompt_tokens=100, total_completion_tokens=20, total_cached_tokens=40,
            total_cost_usd=0.01, total_steps=2, extra=dict(k=1)),
        agent=dict(name='agent-x', version='1.0.0', model_name='model-x', tool_definitions=[dict(type='function')], extra=dict(k=1)))


def raw(document):
    return json.dumps(document).encode()


def edit(document, mutate):
    clone = copy.deepcopy(document)
    mutate(clone)
    return clone
