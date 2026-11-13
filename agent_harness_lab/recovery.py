"""Finalize a completed no-change proposal without repeating a model action."""
import hashlib
import os
import re

from .evaluation.integrity import open_nofollow, open_directory_nofollow, read_regular, relative
from .private_journal import atomic_json, record_bytes

NO_CHANGE_REASONS = ('no_change_harness_supported', 'no_change_evidence_insufficient')


def checkpoint(args, output, result, original, allowed_paths, proposal_path):
    from .workflow import identity, state_root
    if output.parent != state_root()/'runs'/identity(args.project):
        return
    atomic_json(output/'recovery.json', dict(schema_version=1, project=str(args.project),
        configuration_sha256=getattr(args,'configuration_sha256',None), allowed_paths=allowed_paths,
        original=original, proposal=str(proposal_path.relative_to(output)), proposal_sha256=hashlib.sha256(read_regular(proposal_path,1024*1024)).hexdigest(), receipt=result))


def resume(args):
    from . import config
    from .workflow import file_state, harness_files, identity, project_lock, state_root
    if not re.fullmatch(r'run-[a-zA-Z0-9_-]{1,80}', args.resume):
        raise ValueError('resume requires a private run ID')
    output=state_root()/'runs'/identity(args.project)/args.resume
    with project_lock(args.project):
        descriptor=open_directory_nofollow(output)
        try:
            info=os.fstat(descriptor)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('resume requires an owner-only run directory')
        finally: os.close(descriptor)
        try: descriptor=open_nofollow(output/'recovery.json')
        except FileNotFoundError:
            return dict(status='blocked',reason='resume_requires_intervention',output=str(output),
                        automatic_promotion=False,publication_allowed=False)
        try:
            info=os.fstat(descriptor)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 2*1024*1024:
                raise ValueError('unsafe recovery checkpoint')
            with os.fdopen(descriptor,'rb',closefd=False) as stream: raw=stream.read(2*1024*1024+1)
        finally: os.close(descriptor)
        value=record_bytes(raw,2*1024*1024)[0]
        if (set(value) != {'schema_version','project','configuration_sha256','allowed_paths','original','proposal','proposal_sha256','receipt'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['project'] != str(args.project) or not isinstance(value['original'],dict)
                or value['allowed_paths'] is not None and not isinstance(value['allowed_paths'],list)):
            raise ValueError('invalid recovery checkpoint')
        if value['allowed_paths'] is not None:
            if len(value['allowed_paths']) > 1000 or any(relative(name) != name for name in value['allowed_paths']):
                raise ValueError('invalid recovery scope')
        if len(value['original']) > 1000 or any(relative(name) != name for name in value['original']):
            raise ValueError('invalid recovery snapshot')
        path=args.project/'ahl.json'
        settings,actual=config.load(path) if os.path.lexists(path) else (None,None)
        if actual != value['configuration_sha256'] or value['allowed_paths'] != (settings['harness_paths'] if settings else None):
            raise ValueError('project configuration or recovery scope changed since the run')
        files=harness_files(args.project,value['allowed_paths'])
        if any(p.is_symlink() for path in files for p in (path,*path.parents) if p.is_relative_to(args.project)):
            raise ValueError('recovery scope crosses a symlink')
        if len(files) > 1000 or sum(path.stat().st_size for path in files) > 10*1024*1024:
            raise ValueError('recovery snapshot exceeds its limits')
        states={str(path.relative_to(args.project)):list(file_state(path)) for path in files}
        if states != value['original']:
            raise ValueError('project harness changed since the run')
        if not isinstance(value['proposal'],str) or not re.fullmatch(r'preparation/proposal-[01]/proposal\.json',value['proposal']):
            raise ValueError('invalid recovery proposal')
        proposal_raw=read_regular(output/value['proposal'],1024*1024)
        proposal=record_bytes(proposal_raw,1024*1024)[0]
        if hashlib.sha256(proposal_raw).hexdigest() != value['proposal_sha256'] or proposal.get('status') != 'no_change':
            raise ValueError('completed proposal changed since the run')
        receipt=value['receipt']
        if (not isinstance(receipt,dict) or receipt.get('status') != 'no_change'
                or receipt.get('reason') not in NO_CHANGE_REASONS or receipt.get('output') != str(output)
                or receipt.get('changed_files') != [] or receipt.get('automatic_promotion') is not False):
            raise ValueError('invalid recovery receipt')
        atomic_json(output/'workflow.json',receipt)
        return receipt
