"""Private write-ahead harness transactions and conflict-safe manual rollback."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import uuid
from agent_harness_lab.evaluation.integrity import contained, relative
from agent_harness_lab.surfaces import adoption
from agent_harness_lab.monitoring.state import atomic_json


def file_record(path):
    if path.is_symlink(): raise ValueError('symlink conflict')
    if not path.exists(): return None
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>10*1024*1024:
        raise ValueError('file type/link/size conflict')
    if stat.S_IMODE(info.st_mode) & 0o7000:
        raise ValueError('privileged harness modes are unsupported')
    raw=path.read_bytes()
    return dict(sha256=hashlib.sha256(raw).hexdigest(),mode=stat.S_IMODE(info.st_mode),
                content=base64.b64encode(raw).decode())


def in_checkout(project,name):
    """No directory between the project root and name holds a .git entry, so no nested repository owns it."""
    folder=Path(project)
    for part in PurePosixPath(name).parts[:-1]:
        folder=folder/part
        if os.path.lexists(folder/'.git'): return False
    return True


def ignored(project,name):
    """The repository's ignore rules exclude untracked name, directly or through its folder, so it is generated or
    vendored, not owned.

    A read-only `git check-ignore` that runs no hook or fsmonitor and writes nothing; it reads the index only so a
    tracked (force-added) file is never reported. The user's global excludes file is not consulted: it is a personal
    preference, not the project's. A project with no .git at or above it has no ignore rules. Any other git answer,
    a missing git binary or a timeout is unknown and fails closed as ignored."""
    if not PurePosixPath(name).parent.parts: return False
    root=Path(project).absolute()
    if not any(os.path.lexists(parent/'.git') for parent in (root,*root.parents)): return False
    # Inherited GIT_DIR or GIT_WORK_TREE (a git hook sets them) would answer for another repository.
    env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    try:
        result=subprocess.run(['git','-c','core.fsmonitor=false','-c','core.excludesFile=','check-ignore','-q','--',
                               name],
                              cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                              env={**env,'GIT_OPTIONAL_LOCKS':'0'},timeout=10,check=False)
    except (OSError,subprocess.SubprocessError): return True
    return result.returncode!=1


def validate_snapshot(record):
    if record is None: return
    if not isinstance(record,dict) or set(record)!={'sha256','mode','content'}:
        raise ValueError('invalid snapshot envelope')
    if type(record['mode']) is not int or not 0<=record['mode']<=0o777 or not isinstance(record['content'],str):
        raise ValueError('invalid or privileged snapshot mode')
    if len(record['content'])>14*1024*1024: raise ValueError('snapshot byte bound')
    raw=base64.b64decode(record['content'],validate=True)
    if len(raw)>10*1024*1024 or hashlib.sha256(raw).hexdigest()!=record['sha256']:
        raise ValueError('snapshot integrity mismatch')


class GuardedTransactionError(OSError):
    """A guarded transaction needs manual recovery; its journal retains snapshots."""
    def __init__(self, journal, record, remaining):
        super().__init__('guarded transaction restoration requires intervention')
        self.receipt=dict(status='requires_intervention', deployment=record['deployment'],
            journal=str(journal.folder), changed_files=remaining, restoration='conflict',
            active_pointer_conflict=record.get('active_pointer_conflict',False),
            remaining_change_ownership='unverified',
            profile='one-call-improvement-v1', publication_allowed=False)


class Journal:
    def __init__(self,project,folder):
        self.project=Path(project).resolve(); self.folder=Path(folder)
        if self.folder.absolute().is_relative_to(self.project) or self.project.is_relative_to(self.folder.absolute()):
            raise ValueError('private journal and project must not overlap')
        if any(p.is_symlink() for p in (self.folder,*self.folder.absolute().parents)):
            raise ValueError('journal path crosses a symlink')
        self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.folder.chmod(0o700)

    @property
    def active(self):
        path=self.folder/'active.json'
        if not path.exists(): return None
        if path.is_symlink() or path.stat().st_size>1024: raise ValueError('unsafe active journal pointer')
        deployment=json.loads(path.read_text())['deployment']
        if not isinstance(deployment,str) or len(deployment)!=32 or any(c not in '0123456789abcdef' for c in deployment):
            raise ValueError('invalid active deployment identifier')
        return deployment

    def load(self,deployment):
        if not isinstance(deployment,str) or len(deployment)!=32 or any(c not in '0123456789abcdef' for c in deployment):
            raise ValueError('invalid deployment identifier')
        path=self.folder/(deployment+'.json')
        if path.is_symlink() or path.stat().st_size>30*1024*1024: raise ValueError('unsafe journal record')
        record=json.loads(path.read_text())
        if record.get('schema_version')!=1 or record.get('deployment')!=deployment or record.get('kind') not in ('promotion','rollback') or record.get('status') not in ('prepared','applying','committed','conflict'):
            raise ValueError('invalid journal envelope')
        for mapping in (record['before'],record['after']):
            if not isinstance(mapping,dict) or len(mapping)>1000: raise ValueError('invalid snapshot set')
            for name,snapshot in mapping.items():
                validate_snapshot(snapshot)
                # Containment and ignore rules were probed when the record was prepared, so a later .git or
                # .gitignore cannot strand its rollback; a promotion's targets are re-checked with modes and bytes.
                self.owned(name,snapshot if record['kind']=='promotion' and mapping is record['after'] else None,
                           probe=False)
        return record

    def owned(self,name,target=None,*,probe=True):
        """Resolve an owned path. target is a snapshot about to be written (its mode and bytes are checked);
        probe False takes containment and ignore rules as observed when the transaction was prepared."""
        relative(name)
        raw=None if target is None else base64.b64decode(target['content'],validate=True)
        # The root skills/, containment and ignore probes run only for a path whose verdict needs them.
        if adoption(name,None if target is None else target['mode'],raw,
                    root_skills=lambda:(self.project/'skills').is_dir(),
                    in_checkout=(lambda:in_checkout(self.project,name)) if probe else True,
                    ignored=(lambda:ignored(self.project,name)) if probe else False).writability!='adoptable':
            raise ValueError('transaction outside owned harness surface')
        return contained(self.project,name)

    def prepare(self,before,after,metadata=None,kind='promotion'):
        return self._prepare(before,after,metadata,kind,restores=False)

    def _prepare(self,before,after,metadata,kind,*,restores):
        names=sorted(before.keys()|after.keys())
        if not names or len(names)>1000: raise ValueError('invalid transaction size')
        for mapping in (before,after):
            for snapshot in mapping.values(): validate_snapshot(snapshot)
        # Every transaction probes containment and checks what it writes, except a rollback that restores a
        # promotion's original 'before' snapshot (restores, set only by _rollback).
        for name in names:
            relative(name)
            self.owned(name,None if restores else after.get(name),probe=not restores)
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
        # Only an unfinished promotion probes containment again, once before any write; other reads and writes
        # resolve the recorded paths without the probe, so a later nested .git never strands a restoration.
        for name in record['before'].keys()|record['after'].keys():
            path=self.owned(name,probe=record['kind']=='promotion')
            current=file_record(path)
            if current not in (record['before'].get(name),record['after'].get(name)):
                raise ValueError('intervening edit/mode/path conflict')

    def execute(self,deployment):
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.project):
            return self._execute(deployment)

    def _write_snapshot(self,name,current,target,pre_write=None):
        destination=self.owned(name,probe=False)
        if target is None:
            if pre_write: pre_write()
            if file_record(destination)!=current: raise ValueError('target changed during transaction')
            destination.unlink()
        else:
            raw=base64.b64decode(target['content'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=target['sha256']: raise ValueError('journal snapshot corrupted')
            if pre_write: pre_write()
            destination.parent.mkdir(parents=True,exist_ok=True)
            temporary=destination.with_name(destination.name+'.ahl-'+uuid.uuid4().hex)
            try:
                self._pending_snapshot=(temporary,target['sha256'],target['mode'])
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                temporary.chmod(target['mode'])
                if file_record(destination)!=current: raise ValueError('target changed during transaction')
                if pre_write: pre_write()
                os.replace(temporary,destination)
            finally:
                self._pending_snapshot=None
                temporary.unlink(missing_ok=True)

    def _restore_guarded(self,record):
        remaining=[]
        # Inspect every path: interruption may occur after replace but before return.
        for name in sorted(record['before'].keys()|record['after'].keys(),reverse=True):
            before=record['before'].get(name)
            try:
                current=file_record(self.owned(name,probe=False))
                if current==before: continue
                if current!=record['after'].get(name): raise ValueError('intervening change during restoration')
                self._write_snapshot(name,current,before)
                if file_record(self.owned(name,probe=False))!=before: raise ValueError('restoration verification failed')
            except BaseException:
                try:
                    restored=file_record(self.owned(name,probe=False))==before
                except BaseException:
                    restored=False
                if not restored: remaining.append(name)
        pointer_conflict=False
        try:
            if self.active==record['deployment']:
                if record['parent'] is None: (self.folder/'active.json').unlink()
                else: atomic_json(self.folder/'active.json',dict(deployment=record['parent']))
            if self.active!=record['parent']: pointer_conflict=True
        except BaseException:
            pointer_conflict=True
        record.update(status='conflict',guard_rejected=True,restoration='conflict' if remaining or pointer_conflict else 'restored',
                      active_pointer_conflict=pointer_conflict,
                      remaining_changes=remaining)
        try:
            atomic_json(self.folder/(record['deployment']+'.json'),record)
        except BaseException:
            if remaining or pointer_conflict: raise GuardedTransactionError(self,record,remaining)
            raise
        if pointer_conflict: raise GuardedTransactionError(self,record,remaining)
        return remaining

    def _execute(self,deployment,*,pre_write=None):
        # Internal operation for callers already holding the project lock.
        record=self.load(deployment)
        if record.get('guard_rejected'): raise ValueError('rejected guarded transaction cannot be resumed')
        if record['status']=='committed' and self.active==deployment: return record
        if record.get('commit_guarded') and pre_write is None:
            raise ValueError('guarded transaction requires its original commit checks')
        self._check(record)
        try:
            if pre_write: pre_write()
            record['status']='applying'
            if pre_write: record['commit_guarded']=True
            atomic_json(self.folder/(deployment+'.json'),record)
            for name in sorted(record['before'].keys()|record['after'].keys()):
                current=file_record(self.owned(name,probe=False)); target=record['after'].get(name)
                if current==target: continue
                if current!=record['before'].get(name): raise ValueError('target changed during transaction')
                self._write_snapshot(name,current,target,pre_write)
            if any(file_record(self.owned(name,probe=False))!=record['after'].get(name)
                   for name in record['before'].keys()|record['after'].keys()):
                raise ValueError('post-write verification failed')
            if pre_write: pre_write()
            record['status']='committed'; atomic_json(self.folder/(deployment+'.json'),record)
            if pre_write: pre_write()
            atomic_json(self.folder/'active.json',dict(deployment=deployment))
        except BaseException:
            if pre_write:
                try:
                    observed=self.load(deployment)
                    committed=(observed==record and observed['status']=='committed' and self.active==deployment
                        and all(file_record(self.owned(name,probe=False))==record['after'].get(name)
                                for name in record['before'].keys()|record['after'].keys()))
                except BaseException:
                    committed=False
                if committed: return observed
                remaining=self._restore_guarded(record)
                if remaining: raise GuardedTransactionError(self,record,remaining)
            raise
        return record

    def preview_rollback(self,deployment):
        try:
            record=self.load(deployment)
        except ValueError:
            return dict(status='conflict',reason='unsafe_record_or_current_path')
        if self.active!=deployment or record['status']!='committed':
            return dict(status='conflict',reason='deployment_not_active')
        try:
            for name in record['before'].keys()|record['after'].keys():
                if file_record(self.owned(name,probe=False))!=record['after'].get(name):
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
        # Reverting a record whose 'before' is a promotion's 'after' re-applies that content, so it is admitted
        # again like a new promotion; one whose 'before' is the original content restores it without the probe.
        rollback=self._prepare(record['after'],record['before'],dict(reverts=deployment),'rollback',
                               restores=self._before_is_original(record))
        return self._execute(rollback['deployment'])

    def _before_is_original(self,record):
        # Each rollback swaps 'before' and 'after', so the reverts chain's parity back to its promotion says
        # which side holds the original content. An unresolvable chain is treated as re-applying (probed).
        flips,seen=0,set()
        while record['kind']=='rollback':
            reverted=record['metadata'].get('reverts')
            if reverted in seen or len(seen)>=1000: return False
            seen.add(reverted)
            try:
                record=self.load(reverted)
            except (ValueError,OSError,KeyError,TypeError):
                return False
            flips+=1
        return flips%2==0
