"""Private offline planned-campaign records; no executor or budget ledger."""
from pathlib import Path
import secrets

from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory, write_bytes
from .schema import CampaignSpec


def _transition(previous,kind,state,_previous_kind):
    return previous is None and kind == 'created' and state == 'planned'


class CampaignStore(PrivateJournal):
    def __init__(self,root):
        super().__init__(root,'ahl-campaign-store-v1',_transition,max_events=1)

    def create(self,spec,project_root):
        """Create one private planned record without dispatching or touching the project."""
        if type(spec) is not CampaignSpec: raise ValueError('campaign specification required')
        spec.validate()
        project = Path(project_root).absolute()
        if '..' in project.parts or any(path.is_symlink() for path in (project,*project.parents)) or not project.is_dir():
            raise ValueError('existing canonical project directory required')
        if any(path.is_symlink() for path in (self.root,*self.root.parents)):
            raise ValueError('campaign store crosses a symlink')
        if self.root.is_relative_to(project) or project.is_relative_to(self.root):
            raise ValueError('campaign store must be outside the project')
        with self.locked(create=True):
            case_id = secrets.token_hex(32); case = self.case(case_id)
            case.mkdir(mode=0o700,parents=True,exist_ok=False)
            (case/'events').mkdir(mode=0o700)
            write_bytes(case/'spec.json',encoded(spec.to_dict()))
            sync_directory(case)
            events = []
            self.append(case,events,'created','planned',dict(spec_sha256=spec.digest))
            sync_directory(case.parent)
            return self._view(case_id,spec,events)

    def inspect(self,case_id):
        """Inspect only a valid planned record; caller identities remain unverified."""
        with self.locked():
            case = self.case(case_id); events = self.history(case)
            value,_ = record(case/'spec.json'); spec = CampaignSpec.parse(value)
            if events[0][0]['payload'] != dict(spec_sha256=spec.digest):
                raise ValueError('campaign specification changed')
            return self._view(case_id,spec,events)

    @staticmethod
    def _view(case_id,spec,events):
        return dict(profile='ahl-campaign-inspection-v1',campaign_id=case_id,state='planned',spec_sha256=spec.digest,
                    event_sha256=events[-1][1],input_identity='caller_supplied_unverified',budget='not_enforced',
                    execution='not_started',review_decision='not_requested',evaluation_disposition='not_evaluated',
                    deployment_state='not_applied',publication_allowed=False,automatic_promotion=False)
