"""Private write-ahead harness transactions and conflict-safe manual rollback.

A transaction is one deployment unit of whole-file snapshots: every file reaches its after state, or recover()
returns every file to its before state, and any foreign edit is a conflict that writes nothing. key_edit() builds the
after snapshot of a JSON or TOML configuration file that changes only its `hooks` key.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import tomllib
import uuid
from agent_harness_lab.evaluation.integrity import contained, relative
from agent_harness_lab.surfaces import CONTRACT_KINDS, adoption
from agent_harness_lab.monitoring.state import atomic_json


def file_record(path):
    if path.is_symlink(): raise ValueError('symlink conflict')
    if not path.exists(): return None
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>10*1024*1024:
        raise ValueError('file type/link/size conflict')
    if stat.S_IMODE(info.st_mode) & 0o7000:
        raise ValueError('privileged harness modes are unsupported')
    return snapshot_record(path.read_bytes(),stat.S_IMODE(info.st_mode))


def snapshot_record(raw,mode):
    """The file_record envelope for bytes about to be written with st_mode bits mode."""
    return dict(sha256=hashlib.sha256(raw).hexdigest(),mode=mode,content=base64.b64encode(raw).decode())


KEY='hooks'
MAX_KEY_EDIT_BYTES=1024*1024
_WS=re.compile(r'[ \t\n\r]*')
_SPACE=re.compile(r'[ \t]*')
_BARE=re.compile(r'[A-Za-z0-9_-]+')
_ROOT_KEY=re.compile(r'[ \t]*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)[ \t]*[.=]')
_HEADER=re.compile(r'[ \t]*(\[\[?)([^\[\]]*)(\]\]?)[ \t]*(?:#.*)?')


def _json_members(text):
    """Top-level members of one JSON object as (key, key_start, value_start, value_end), and its closing index."""
    decoder,members=json.JSONDecoder(),[]
    index=_WS.match(text).end()
    if text[index:index+1]!='{': raise ValueError('configuration must be a JSON object')
    index=_WS.match(text,index+1).end()
    if text[index:index+1]=='}': return members,index
    while True:
        if text[index:index+1]!='"': raise ValueError('invalid JSON object key')
        key,end=json.decoder.scanstring(text,index+1)
        end=_WS.match(text,end).end()
        if text[end:end+1]!=':': raise ValueError('invalid JSON object member')
        start=_WS.match(text,end+1).end()
        _,stop=decoder.raw_decode(text,start)
        members.append((key,index,start,stop))
        index=_WS.match(text,stop).end()
        if text[index:index+1]=='}': return members,index
        if text[index:index+1]!=',': raise ValueError('invalid JSON object separator')
        index=_WS.match(text,index+1).end()


def _indent(text,position):
    line=text[text.rfind('\n',0,position)+1:position]
    return line if not line.strip() else ''


def _json_edit(text,value):
    members,close=_json_members(text)
    found=[index for index,member in enumerate(members) if member[0]==KEY]
    if len(found)>1: raise ValueError('duplicate structured key')
    dumped=lambda indent:json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False).replace('\n','\n'+indent)
    if found:
        index=found[0]; _,start,value_start,stop=members[index]
        if value is not None: return text[:value_start]+dumped(_indent(text,start))+text[stop:]
        if index+1<len(members): return text[:start]+text[members[index+1][1]:]
        if index: return text[:members[index-1][3]]+text[stop:]
        return text[:text.index('{')+1]+text[close:]
    if value is None: return text
    if not members: return text[:text.index('{')+1]+'\n  '+json.dumps(KEY)+': '+dumped('  ')+'\n'+text[close:]
    indent=_indent(text,members[-1][1]); stop=members[-1][3]
    return text[:stop]+',\n'+indent+json.dumps(KEY)+': '+dumped(indent)+text[stop:]


def _toml_key(text):
    """Segments of a dotted TOML key; anything but bare, basic or literal segments fails closed."""
    parts,index,text=[],0,text.strip()
    while True:
        bare=_BARE.match(text,index)
        if bare: parts.append(bare[0]); index=bare.end()
        elif text[index:index+1]=='"': value,index=json.decoder.scanstring(text,index+1); parts.append(value)
        elif text[index:index+1]=="'":
            end=text.index("'",index+1); parts.append(text[index+1:end]); index=end+1
        else: raise ValueError('unsupported TOML key')
        index=_SPACE.match(text,index).end()
        if index==len(text): return tuple(parts)
        if text[index]!='.': raise ValueError('unsupported TOML key')
        index=_SPACE.match(text,index+1).end()


def _toml_scalar(value):
    if isinstance(value,bool): return 'true' if value else 'false'
    if isinstance(value,int): return str(value)
    if isinstance(value,str): return json.dumps(value,ensure_ascii=False)
    if isinstance(value,list) and not any(isinstance(item,(dict,list)) for item in value):
        return '['+', '.join(_toml_scalar(item) for item in value)+']'
    raise ValueError('unsupported TOML hook value')


def _toml_tables(path,table,array=False):
    """Sections of one table: its scalar keys under a header, then each sub-table and array of tables."""
    nested=lambda value:isinstance(value,dict) or isinstance(value,list) and value and all(isinstance(i,dict) for i in value)
    name='.'.join(key if _BARE.fullmatch(key) else json.dumps(key,ensure_ascii=False) for key in path)
    lines=['[['+name+']]' if array else '['+name+']']
    lines+=[f'{key if _BARE.fullmatch(key) else json.dumps(key,ensure_ascii=False)} = {_toml_scalar(value)}'
            for key,value in table.items() if not nested(value)]
    sections=[lines] if array or len(lines)>1 or not table else []
    for key,value in table.items():
        if isinstance(value,dict): sections+=_toml_tables(path+(key,),value)
        elif nested(value): sections+=[section for item in value for section in _toml_tables(path+(key,),item,True)]
    return sections


def _toml_edit(text,value):
    """Drop every [hooks...] section except trust state, then append the new table; other bytes stay in place.

    The trailing blank and comment lines of a dropped section introduce the next section, so they stay."""
    result,pending,dropping,root=[],[],False,True
    def flush():
        body=[index for index,row in enumerate(pending) if row.strip() and not row.lstrip().startswith('#')]
        result.extend(pending[body[-1]+1:] if dropping and body else pending)
        pending.clear()
    for line in text.splitlines(keepends=True):
        header=_HEADER.fullmatch(line.rstrip('\r\n'))
        if header and len(header[1])==len(header[3]):
            path=_toml_key(header[2]); flush(); root=False
            dropping=path[0]==KEY and path[1:2]!=('state',)
            if not dropping: result.append(line)
            continue
        key=_ROOT_KEY.match(line) if root else None
        if key and _toml_key(key[1])[0]==KEY: raise ValueError('unsupported root hooks layout')
        pending.append(line)
    flush()
    body=''.join(result)
    if value is None: return body
    if body and not body.endswith('\n'): body+='\n'
    sections=_toml_tables((KEY,),value)
    return body+('\n' if body.strip() else '')+'\n\n'.join('\n'.join(section) for section in sections)+'\n'


def key_edit(raw,form,value):
    """Rewrite only the top-level `hooks` key of JSON or TOML bytes (raw None: an absent file); value None removes it.

    Every other member keeps its bytes. TOML trust state ([hooks.state]) is never written: it stays byte-identical and
    a value carrying it is refused. The result is re-parsed and refused unless only the hooks key differs."""
    if raw is not None and (not isinstance(raw,bytes) or len(raw)>MAX_KEY_EDIT_BYTES): raise ValueError('configuration byte bound')
    if value is not None and not isinstance(value,dict): raise ValueError('hooks value must be an object')
    text='' if raw is None else raw.decode('utf-8')
    if form=='json':
        text=text if text.strip() else '{}\n'
        old=_strict_json(text); new_text=_json_edit(text,value); new=_strict_json(new_text)
        if [(m[0],text[m[1]:m[3]]) for m in _json_members(text)[0] if m[0]!=KEY]!=\
                [(m[0],new_text[m[1]:m[3]]) for m in _json_members(new_text)[0] if m[0]!=KEY]:
            raise ValueError('structured edit changed another key')
        expected=value
    elif form=='toml':
        if value is not None and 'state' in value: raise ValueError('hook trust state is never written')
        old=tomllib.loads(text); new_text=_toml_edit(text,value); new=tomllib.loads(new_text)
        state=old.get(KEY,{}).get('state') if isinstance(old.get(KEY),dict) else None
        expected=None if value is None and state is None else dict(value or {},**({} if state is None else {'state':state}))
    else: raise ValueError('unsupported structured edit format')
    if {k:v for k,v in old.items() if k!=KEY}!={k:v for k,v in new.items() if k!=KEY} or new.get(KEY)!=expected \
            or (KEY in new)!=(expected is not None):
        raise ValueError('structured edit did not change exactly the hooks key')
    return new_text.encode()


def _strict_json(text):
    from agent_harness_lab.sources.hardened import load_json
    return load_json(text.encode(),dict(max_json_depth=32,max_json_containers=20000,max_json_values=100000),'configuration')


def in_checkout(project,name):
    """No directory between the project root and name holds a .git entry, so no nested repository owns it."""
    folder=Path(project)
    for part in PurePosixPath(name).parts[:-1]:
        folder=folder/part
        if os.path.lexists(folder/'.git'): return False
    return True


def ignored(project,name):
    """The repository's ignore rules exclude the folder holding untracked name, so it is generated or vendored, not
    owned.

    Only directory-level rules decide: a file-name pattern such as `CLAUDE.md` keeps instruction files local, it does
    not make their folder foreign. Read-only git calls that run no hook or fsmonitor and write nothing: `check-ignore
    --no-index` on the folder, then, only for an ignored folder, `ls-files --error-unmatch` so a tracked
    (force-added) file is never reported. The user's global excludes file is not consulted: it is a personal
    preference, not the project's. A project with no .git at or above it has no ignore rules. Any other git answer,
    a missing git binary or a timeout is unknown and fails closed as ignored."""
    folder=PurePosixPath(name).parent
    if not folder.parts: return False
    root=Path(project).absolute()
    if not any(os.path.lexists(parent/'.git') for parent in (root,*root.parents)): return False
    # Inherited GIT_DIR or GIT_WORK_TREE (a git hook sets them) would answer for another repository.
    env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    def git(*args):
        return subprocess.run(['git','-c','core.fsmonitor=false','-c','core.excludesFile=',*args],cwd=root,
                              stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                              env={**env,'GIT_OPTIONAL_LOCKS':'0'},timeout=10,check=False).returncode
    try:
        verdict=git('check-ignore','-q','--no-index','--',f'{folder}/')
        if verdict==1: return False
        if verdict!=0: return True
        # A literal pathspec, so a glob character in name never matches another tracked file.
        return git('--literal-pathspecs','ls-files','--error-unmatch','--',name)!=0
    except (OSError,subprocess.SubprocessError): return True


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
        admitted=self._admitted(record)
        for mapping in (record['before'],record['after']):
            for name,snapshot in mapping.items():
                validate_snapshot(snapshot)
                # Containment and ignore rules were probed when the record was prepared, so a later .git or
                # .gitignore cannot strand its rollback; a promotion's targets are re-checked with modes and bytes.
                self.owned(name,snapshot if record['kind']=='promotion' and mapping is record['after'] else None,
                           probe=False,admitted=admitted)
        return record

    def _admitted(self,record,*,prepare=False):
        """Paths a passed hook contract admitted: exactly the record's whole deployment unit, or none. A promotion's
        admission binds the sha256 and mode of every member snapshot and is sealed with the journal key when prepared;
        a rollback's is its reverted deployment's, and only the exact reversal of that sealed deployment carries it."""
        metadata=record.get('metadata') or {}
        contract=metadata.get('hook_contract')
        if contract is None: return frozenset()
        from agent_harness_lab.hooks_contract import PROFILES,unit_snapshots
        names=sorted(record['before'].keys()|record['after'].keys())
        if not isinstance(contract,dict) or contract.get('profile') not in PROFILES or contract.get('status')!='passed' \
                or contract.get('members')!=names:
            raise ValueError('invalid hook contract admission')
        if record.get('kind')=='rollback':
            # The reverted record was sealed when it was prepared, so its before bytes are what the disk held then.
            try: reverted=self.load(metadata.get('reverts'))
            except OSError: raise ValueError('rollback admission differs from the reverted deployment') from None
            if (prepare and reverted['status']!='committed') or reverted['metadata'].get('hook_contract')!=contract \
                    or record['before']!=reverted['after'] or record['after']!=reverted['before']:
                raise ValueError('rollback admission differs from the reverted deployment')
        elif record.get('kind')!='promotion' or contract.get('snapshots')!=[
                list(row) for row in unit_snapshots(record['before'],record['after'])]:
            raise ValueError('hook contract admission does not bind this deployment unit')
        elif not prepare:
            seal=metadata.get('hook_contract_seal')
            if not isinstance(seal,str) or not hmac.compare_digest(seal,self._seal(record.get('deployment'),contract)):
                raise ValueError('hook contract admission is not authenticated')
        return frozenset(names)

    def _admission_key(self,*,create=False):
        """The journal's private key for admission seals. It lives beside the records, outside every record, so a
        record whose snapshots and admission rows are rewritten together no longer carries a valid seal."""
        path=self.folder/'admission.key'
        if create and not os.path.lexists(path):
            temporary=self.folder/('.admission-'+uuid.uuid4().hex)
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(secrets.token_bytes(32)); stream.flush(); os.fsync(stream.fileno())
                try: os.link(temporary,path)
                except FileExistsError: pass
            finally:
                temporary.unlink(missing_ok=True)
        try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        except OSError: raise ValueError('hook contract admission is not authenticated') from None
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077 \
                    or info.st_size!=32:
                raise ValueError('unsafe hook admission key')
            return stream.read(32)

    def _seal(self,deployment,contract,*,create=False):
        """Keyed digest of one promotion's admission, which binds the sha256 and mode of every member snapshot."""
        message=json.dumps(dict(deployment=deployment,admission=contract),sort_keys=True,separators=(',',':')).encode()
        return hmac.new(self._admission_key(create=create),message,hashlib.sha256).hexdigest()

    def owned(self,name,target=None,*,probe=True,admitted=frozenset()):
        """Resolve an owned path. target is a snapshot about to be written (its mode and bytes are checked);
        probe False takes containment and ignore rules as observed when the transaction was prepared. admitted
        holds the members of a deployment unit a passed executable contract admitted; only such a member may be a
        contract_required hook or workflow (surfaces.CONTRACT_KINDS)."""
        relative(name)
        raw=None if target is None else base64.b64decode(target['content'],validate=True)
        # The root skills/, containment and ignore probes run only for a path whose verdict needs them.
        verdict=adoption(name,None if target is None else target['mode'],raw,
                         root_skills=lambda:(self.project/'skills').is_dir(),
                         in_checkout=(lambda:in_checkout(self.project,name)) if probe else True,
                         ignored=(lambda:ignored(self.project,name)) if probe else False)
        if verdict.writability!='adoptable' and not (
                name in admitted and verdict.writability=='contract_required'
                and any(kind in verdict.facets for kind in CONTRACT_KINDS)):
            raise ValueError('transaction outside owned harness surface')
        return contained(self.project,name)

    def prepare(self,before,after,metadata=None,kind='promotion',*,hook_contract=None):
        """hook_contract is the caller's explicit hook_contract_v1 verdict; the journal re-evaluates it and admits
        the unit only when both agree and it passed. A rollback admits exactly what its reverted record admitted, and
        only for the exact reversal of that record's snapshots."""
        return self._prepare(before,after,metadata,kind,restores=False,hook_contract=hook_contract)

    def _prepare(self,before,after,metadata,kind,*,restores,hook_contract=None):
        names=sorted(before.keys()|after.keys())
        if not names or len(names)>1000: raise ValueError('invalid transaction size')
        for mapping in (before,after):
            for snapshot in mapping.values(): validate_snapshot(snapshot)
        # Every transaction probes containment and checks what it writes, except a rollback that restores a
        # promotion's original 'before' snapshot (restores, set only by rollback). A rollback admits a hook unit
        # only as its reverted record did.
        promotion=kind=='promotion'
        metadata=dict(metadata or {})
        if (kind!='rollback' and 'hook_contract' in metadata) or 'hook_contract_seal' in metadata:
            raise ValueError('hook contract admission is never declared')
        if hook_contract is not None:
            from agent_harness_lab.hooks_contract import evaluate
            verdict=evaluate(self.project,before,after) if promotion else None
            if verdict is None or verdict!=hook_contract or verdict.status!='passed':
                raise ValueError('hook contract did not pass for this deployment unit')
            metadata['hook_contract']=verdict.admission()
        admitted=self._admitted(dict(kind=kind,before=before,after=after,metadata=metadata),prepare=True)
        for name in names:
            relative(name)
            self.owned(name,None if restores else after.get(name),probe=not restores,admitted=admitted)
            if before.get(name)!=file_record(self.project/name): raise ValueError('project changed before journaling')
        if sum(len((record or {}).get('content','')) for mapping in (before,after) for record in mapping.values())>28*1024*1024:
            raise ValueError('journal snapshot limit')
        deployment=uuid.uuid4().hex
        if admitted and promotion: metadata['hook_contract_seal']=self._seal(deployment,metadata['hook_contract'],create=True)
        record=dict(schema_version=1,deployment=deployment,parent=self.active,kind=kind,
                    status='prepared',before=before,after=after,metadata=metadata)
        atomic_json(self.folder/(deployment+'.json'),record)
        return record

    def _check(self,record):
        if self.active not in (record['parent'],record['deployment']): raise ValueError('newer deployment conflict')
        # Only an unfinished promotion probes containment again, once before any write; other reads and writes
        # resolve the recorded paths without the probe, so a later nested .git never strands a restoration.
        admitted=self._admitted(record)
        for name in record['before'].keys()|record['after'].keys():
            path=self.owned(name,probe=record['kind']=='promotion',admitted=admitted)
            current=file_record(path)
            if current not in (record['before'].get(name),record['after'].get(name)):
                raise ValueError('intervening edit/mode/path conflict')

    def execute(self,deployment):
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.project):
            return self._execute(deployment)

    def _write_snapshot(self,name,current,target,pre_write=None):
        # The admitted hook unit of the transaction being written; empty outside _execute and _restore_guarded.
        destination=self.owned(name,probe=False,admitted=getattr(self,'_unit',frozenset()))
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
        remaining=[]; admitted=self._admitted(record)
        owned=lambda name:self.owned(name,probe=False,admitted=admitted)
        # Inspect every path: interruption may occur after replace but before return.
        for name in sorted(record['before'].keys()|record['after'].keys(),reverse=True):
            before=record['before'].get(name)
            try:
                current=file_record(owned(name))
                if current==before: continue
                if current!=record['after'].get(name): raise ValueError('intervening change during restoration')
                self._unit=admitted
                try: self._write_snapshot(name,current,before)
                finally: self._unit=frozenset()
                if file_record(owned(name))!=before: raise ValueError('restoration verification failed')
            except BaseException:
                try:
                    restored=file_record(owned(name))==before
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
        admitted=self._admitted(record)
        if admitted and record['kind']=='promotion':
            from agent_harness_lab.hooks_contract import evaluate
            # The recorded bytes and the checkout scripts as they are now must still give the admitted verdict.
            if evaluate(self.project,record['before'],record['after']).admission()!=record['metadata']['hook_contract']:
                raise ValueError('hook contract did not pass for this deployment unit')
        owned=lambda name:self.owned(name,probe=False,admitted=admitted)
        try:
            if pre_write: pre_write()
            record['status']='applying'
            if pre_write: record['commit_guarded']=True
            atomic_json(self.folder/(deployment+'.json'),record)
            for name in sorted(record['before'].keys()|record['after'].keys()):
                current=file_record(owned(name)); target=record['after'].get(name)
                if current==target: continue
                if current!=record['before'].get(name): raise ValueError('target changed during transaction')
                self._unit=admitted
                try: self._write_snapshot(name,current,target,pre_write)
                finally: self._unit=frozenset()
            if any(file_record(owned(name))!=record['after'].get(name)
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
                        and all(file_record(owned(name))==record['after'].get(name)
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
            admitted=self._admitted(record)
            for name in record['before'].keys()|record['after'].keys():
                if file_record(self.owned(name,probe=False,admitted=admitted))!=record['after'].get(name):
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
        admission={'hook_contract':record['metadata']['hook_contract']} if 'hook_contract' in record['metadata'] else {}
        rollback=self._prepare(record['after'],record['before'],dict(reverts=deployment,**admission),'rollback',
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

    def recover(self,deployment):
        """Return an unfinished deployment to its before state as one unit. A foreign edit to any member, or a
        newer deployment, is a conflict raised before any write, so the user's work is preserved."""
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.project):
            record=self.load(deployment)
            if record['status'] not in ('prepared','applying') or record.get('guard_rejected'):
                raise ValueError('only an unfinished deployment can be recovered')
            self._check(record)
            remaining=self._restore_guarded(record)
            if remaining: raise GuardedTransactionError(self,record,remaining)
            return dict(status='recovered',deployment=deployment,
                        files=sorted(record['before'].keys()|record['after'].keys()))
