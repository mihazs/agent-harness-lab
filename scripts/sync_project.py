#!/usr/bin/env python3
"""Refresh label mirrors and PR review/CI snapshots; no background work or status edits."""
import argparse
import json
import subprocess

REPO = 'mihazs/agent-harness-lab'
AXES = {
    'Priority': {f'priority: P{n}': f'P{n}' for n in range(4)},
    'Workstream': {'track: science': 'Scientific Reliability',
                   'track: data sources': 'Data Sources',
                   'track: product': 'Product & DX',
                   'track: community': 'Community & Growth'},
    'Roadmap': {f'roadmap: {n.lower()}': n for n in ('Now', 'Next', 'Later')},
}


def graphql(query, variables):
    result = subprocess.run(['gh', 'api', 'graphql', '--input', '-'],
                            input=json.dumps({'query': query, 'variables': variables}),
                            text=True, capture_output=True)
    if not result.stdout:
        raise RuntimeError('GitHub CLI failed; check gh auth status and network access')
    data = json.loads(result.stdout)
    if result.returncode or data.get('errors'):
        raise ValueError(data.get('errors', 'GitHub CLI request failed'))
    return data['data']


def mirrored_values(labels):
    values = {}
    for field, mapping in AXES.items():
        found = [value for label, value in mapping.items() if label in labels]
        if len(found) > 1:
            raise ValueError(f'Conflicting {field} labels')
        values[field] = found[0] if found else None
    return values


def review_summary(pr):
    commits = pr['commits']['nodes']
    rollup = commits[-1]['commit']['statusCheckRollup'] if commits else None
    ci = rollup['state'] if rollup else 'NOT_REPORTED'
    review = 'DRAFT' if pr['isDraft'] else pr['reviewDecision'] or 'NO_REVIEW_REPORTED'
    return f'{review}; CI {ci}; merge {pr["mergeStateStatus"]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='write changes (default: preview)')
    args = parser.parse_args()
    project = graphql('''query {user(login:"mihazs"){projectV2(number:3){id
      repositories(first:100){nodes{nameWithOwner} pageInfo{hasNextPage}}
      fields(first:100){nodes{... on ProjectV2Field{id name}
      ... on ProjectV2SingleSelectField{id name options{id name}}} pageInfo{hasNextPage}}
    }}}''', {})['user']['projectV2']
    if project['repositories']['pageInfo']['hasNextPage'] or project['fields']['pageInfo']['hasNextPage']:
        raise ValueError('Project configuration is truncated')
    if REPO not in [r['nameWithOwner'] for r in project['repositories']['nodes']]:
        raise ValueError('Project is not linked to the expected repository')
    fields = {f['name']: f for f in project['fields']['nodes']}
    for name, mapping in AXES.items():
        if not set(mapping.values()) <= {o['name'] for o in fields[name]['options']}:
            raise ValueError(f'Missing {name} options')
    changes = []
    cursor = None
    while True:
        page = graphql('''query($id:ID!,$cursor:String){node(id:$id){... on ProjectV2{
          items(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id
          fieldValues(first:100){pageInfo{hasNextPage} nodes{
            ... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2SingleSelectField{name}}}
            ... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2Field{name}}}}}
          content{... on Issue{repository{nameWithOwner} labels(first:100){nodes{name} pageInfo{hasNextPage}}}
          ... on PullRequest{repository{nameWithOwner} labels(first:100){nodes{name} pageInfo{hasNextPage}}
            state isDraft reviewDecision mergeStateStatus commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}
        }}}}''', {'id': project['id'], 'cursor': cursor})['node']['items']
        for item in page['nodes']:
            content = item['content']
            if not content or content.get('repository', {}).get('nameWithOwner') != REPO:
                continue
            if content['labels']['pageInfo']['hasNextPage'] or item['fieldValues']['pageInfo']['hasNextPage']:
                raise ValueError('Item labels or values are truncated')
            desired = mirrored_values({l['name'] for l in content['labels']['nodes']})
            if 'state' in content:
                desired['Review & CI'] = review_summary(content) if content['state'] == 'OPEN' else None
            current = {v['field']['name']: v.get('name', v.get('text'))
                       for v in item['fieldValues']['nodes'] if 'field' in v}
            for name, value in desired.items():
                if current.get(name) == value:
                    continue
                field = fields[name]
                payload = {'projectId': project['id'], 'itemId': item['id'], 'fieldId': field['id']}
                if value is not None:
                    payload['value'] = ({'text': value} if name == 'Review & CI' else
                                        {'singleSelectOptionId': next(o['id'] for o in field['options'] if o['name'] == value)})
                changes.append((name, value, payload))
        if not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']
    # Validate the complete snapshot before any mutation; rerun after concurrent label edits.
    for name, value, payload in changes:
        print(f'{payload["itemId"]}: {name} -> {value or "(unset)"}')
        if args.apply:
            operation = 'update' if value is not None else 'clear'
            graphql('mutation($input:' + operation.title() + 'ProjectV2ItemFieldValueInput!){' +
                    operation + 'ProjectV2ItemFieldValue(input:$input){projectV2Item{id}}}', {'input': payload})
    print(f'{len(changes)} field changes {"applied" if args.apply else "proposed"}')


if __name__ == '__main__':
    main()
