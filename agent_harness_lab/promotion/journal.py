"""Private write-ahead harness transactions and conflict-safe manual rollback."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from agent_harness_lab.evaluation.integrity import contained, relative
from agent_harness_lab.monitoring.state import atomic_json


def file_record(path):
    if path.is_symlink(): raise ValueError('symlink conflict')
    if not path.exists(): return None
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>10*1024*1024:
        raise ValueError('file type/link/size conflict')
    raw=path.read_bytes()
    return dict(sha256=hashlib.sha256(raw).hexdigest(),mode=stat.S_IMODE(info.st_mode),
                content=base64.b64encode(raw).decode())


class Journal:
    def __init__(self,project,folder):
        self.project=Path(project).resolve(); self.folder=Path(folder)
        if any(p.is_symlink() for p in (self.folder,*self.folder.absolute().parents)):
            raise ValueError('journal path crosses a symlink')
        self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.folder.chmod(0o700)

    @property
    def active(self):
        path=self.folder/'active.json'
        return json.loads(path.read_text())['deployment'] if path.exists() else None

    def load(self,deployment):
        if not isinstance(deployment,str) or len(deployment)!=32 or any(c not in '0123456789abcdef' for c in deployment):
            raise ValueError('invalid deployment identifier')
        path=self.folder/(deployment+'.json')
        if path.is_symlink() or path.stat().st_size>30*1024*1024: raise ValueError('unsafe journal record')
        return json.loads(path.read_text())

    def owned(self,name):
        relative(name)
        parts=Path(name).parts
        allowed=name in ('AGENTS.md','CLAUDE.md') or (len(parts)>=4 and parts[:2] in (('.agents','skills'),('.claude','skills')))
        allowed |= len(parts)>=3 and parts[0]=='skills' and (self.project/'skills').is_dir()
        if not allowed or any(p in ('.git','__pycache__') for p in parts):
            raise ValueError('transaction outside owned harness surface')
        return contained(self.project,name)

    def prepare(self,before,after,metadata=None,kind='promotion'):
        names=sorted(before.keys()|after.keys())
        if not names or len(names)>1000: raise ValueError('invalid transaction size')
        for name in names:
            relative(name)
            self.owned(name)
            if before.get(name)!=file_record(self.project/name): raise ValueError('project changed before journaling')
        if sum(len((record or {}).get('content','')) for mapping in (before,after) for record in mapping.values())>28*1024*1024:
            raise ValueError('journal snapshot limit')
        deployment=uuid.uuid4().hex
        record=dict(schema_version=1,deployment=deployment,parent=self.active,kind=kind,
                    status='prepared',before=before,after=after,metadata=metadata or {})
        atomic_json(self.folder/(deployment+'.json'),record)
        return record

    def _check(self,record):
        if self.active not in (record['parent'],record['deployment']): raise ValueError('newer deployment conflict')
        for name in record['before'].keys()|record['after'].keys():
            path=self.owned(name)
            current=file_record(path)
            if current not in (record['before'].get(name),record['after'].get(name)):
                raise ValueError('intervening edit/mode/path conflict')

    def execute(self,deployment):
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.project):
            return self._execute(deployment)

    def _execute(self,deployment):
        # Internal operation for callers already holding the project lock.
        record=self.load(deployment)
        if record['status']=='committed' and self.active==deployment: return record
        self._check(record)
        record['status']='applying'; atomic_json(self.folder/(deployment+'.json'),record)
        for name in sorted(record['before'].keys()|record['after'].keys()):
            destination=self.owned(name)
            target=record['after'].get(name)
            current=file_record(destination)
            if current==target: continue
            if current!=record['before'].get(name): raise ValueError('target changed during transaction')
            if target is None:
                destination.unlink()
            else:
                raw=base64.b64decode(target['content'],validate=True)
                if hashlib.sha256(raw).hexdigest()!=target['sha256']: raise ValueError('journal snapshot corrupted')
                destination.parent.mkdir(parents=True,exist_ok=True)
                temporary=destination.with_name(destination.name+'.ahl-'+uuid.uuid4().hex)
                try:
                    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    temporary.chmod(target['mode'])
                    if file_record(destination)!=current: raise ValueError('target changed during transaction')
                    os.replace(temporary,destination)
                finally:
                    temporary.unlink(missing_ok=True)
        if any(file_record(self.owned(name))!=record['after'].get(name)
               for name in record['before'].keys()|record['after'].keys()):
            raise ValueError('post-write verification failed')
        record['status']='committed'; atomic_json(self.folder/(deployment+'.json'),record)
        atomic_json(self.folder/'active.json',dict(deployment=deployment))
        return record

    def preview_rollback(self,deployment):
        record=self.load(deployment)
        if self.active!=deployment or record['status']!='committed':
            return dict(status='conflict',reason='deployment_not_active')
        try:
            for name in record['before'].keys()|record['after'].keys():
                if file_record(self.owned(name))!=record['after'].get(name):
                    raise ValueError('intervening edit or mode change')
        except ValueError:
            return dict(status='conflict',reason='intervening_file_path_or_mode_change')
        return dict(status='rollback_eligible',deployment=deployment,files=sorted(record['before'].keys()|record['after'].keys()))

    def rollback(self,deployment):
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.project):
            return self._rollback(deployment)

    def _rollback(self,deployment):
        active=self.active
        if active:
            latest=self.load(active)
            if latest['kind']=='rollback' and latest['metadata'].get('reverts')==deployment:
                return latest
        if self.preview_rollback(deployment)['status']!='rollback_eligible': raise ValueError('rollback conflict')
        record=self.load(deployment)
        rollback=self.prepare(record['after'],record['before'],dict(reverts=deployment),'rollback')
        return self._execute(rollback['deployment'])
