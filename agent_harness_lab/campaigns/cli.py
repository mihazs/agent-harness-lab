"""Offline creation and inspection of private planned campaigns."""
import argparse
import json
from pathlib import Path

from agent_harness_lab.private_journal import record
from .schema import CampaignSpec
from .store import CampaignStore


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl campaign',description=(
        'Create or inspect private planned campaigns offline. No execution, budget enforcement, review, recovery or deployment is implemented. '
        'Project and store ancestry must remain controlled and stable during creation.'))
    parser.add_argument('--store',type=Path,required=True,help='Explicit private owner-only store outside the project')
    actions = parser.add_subparsers(dest='action',required=True)
    create = actions.add_parser('create',help='Freeze a supplied specification without launching work')
    create.add_argument('--spec',type=Path,required=True,help='Existing private campaign specification JSON')
    create.add_argument('--project-root',type=Path,required=True,help='Existing project directory; keep its and the store ancestry stable during creation')
    inspect = actions.add_parser('inspect',help='Inspect one private planned record without launching work')
    inspect.add_argument('--campaign',required=True,help='Opaque campaign ID returned by create')
    args = parser.parse_args(argv)
    try:
        store = CampaignStore(args.store)
        if args.action == 'create':
            value,_ = record(args.spec,64*1024)
            result = store.create(CampaignSpec.parse(value),args.project_root)
        else:
            result = store.inspect(args.campaign)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0
    except BlockingIOError:
        print(json.dumps(dict(status='busy',publication_allowed=False,automatic_promotion=False)))
        return 3
    except (ValueError,OSError,TypeError,KeyError,RecursionError):
        parser.exit(2,'campaign error: invalid or unavailable private input; campaign history retained\n')
