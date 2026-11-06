"""Explicit live producer ingestion; journals retain declarations, never authority."""
from dataclasses import replace
import secrets

from agent_harness_lab.evaluation import controller_receipts as owned
from . import harness_binding
from .execution import CoverageManifest, ExecutionIdentity, ExecutionMetadata, IDENTITY_FIELDS, IdentityField, MissingField, RecordRef, ScanBound
from .graph import EvidenceGraph, JoinReceipt
from .registry import Capability, Registry
from .schema import EvidenceRecord, SourceSpec, digest, fingerprint
from .snapshot import Snapshot
from .store import SourceStore


class ControllerCapture:
    def __init__(self, source, capture):
        self.source, self.capture = source, capture

    def read(self, source):
        if (source != self.source or not source.enabled or source.kind!='controller'
                or source.provider!='ahl-controller' or source.version!='v1' or type(self.capture) is not owned._Attempt):
            raise ValueError('explicit live controller source required')
        data = self.capture.read()
        if data['source_sha256']!=source.source_key: raise ValueError('controller capture scope mismatch')
        grade = data['grade']; kernels = data['kernels']
        records=[]
        for name,ordinal,plane in (('executor',grade['executor_ordinal'],'behavioral_event'),
                                    ('grader',grade['grader_ordinal'],'grader_reference')):
            identity = digest((data['attempt_sha256'],name)); revision = digest((kernels[ordinal],grade))
            records.append(EvidenceRecord(source.source_key,identity,revision,revision,plane,'controller_'+name,
                data['closed_at'],original_at=kernels[ordinal]['observed_at'],provenance='native_field',capture_completeness='partial'))
        executor,grader = (RecordRef(record.source_sha256,record.native_id_sha256,record.revision_sha256) for record in records)
        kernel = kernels[grade['executor_ordinal']]
        values = (('session','ahl.controller_run',data['session_sha256']),
            ('attempt','ahl.controller_attempt',data['attempt_sha256']),
            ('runtime','ahl.binary_path_inventory',kernel['binary_sha256']),
            ('runtime_version','ahl.worker_rootfs',kernel['image_sha256']),
            ('grader_revision','ahl.frozen_grader',grade['oracle_sha256']))
        fields=tuple(IdentityField(name,namespace,value,'declared','controller_observation',(executor,grader),
            reason='no_effective_binding' if name=='runtime' else 'native_metadata',observed_at=data['closed_at']) for name,namespace,value in values)
        fields += (IdentityField('project','ahl.declared_project',source.project_sha256,'declared','source_declared',
            (executor,),reason='source_declaration'),)
        coverage=CoverageManifest(source.source_key,source.scope_sha256,source.scope_sha256,source.version,owned.PROFILE,
            (ScanBound('records',owned.MAX_WORKERS,len(kernels)),
             ScanBound('bytes',owned.MAX_RECEIPT_BYTES,len(owned.encoded(data))),
             ScanBound('files',100,len(self.capture.expected))),
            missing_fields=tuple(MissingField(name,'no_effective_binding') for name in IDENTITY_FIELDS if name=='runtime' or name not in {field.name for field in fields}),
            gaps=('no_effective_binding',),reported_completeness='partial',limit_reached=len(kernels)==owned.MAX_WORKERS)
        metadata=ExecutionMetadata((ExecutionIdentity(executor,fields),),coverage)
        join=JoinReceipt(grader,executor,'same_execution','ahl.controller',owned.PROFILE,(executor,grader),basis='native_fields')
        graph=EvidenceGraph(source.source_key,source.project_sha256,source.scope_sha256,source.version,(executor,grader),(join,))
        metadata.validate(source,records); graph.validate(source,records)
        return Snapshot(digest(data),tuple(records),metadata,graph)


def recheck(source, connector, proposed):
    from .resolve import _recheck
    if type(connector) is not ControllerCapture: raise ValueError('exact live controller reader required')
    snapshot=ControllerCapture.read(connector,source)
    if proposed!=snapshot.graph: raise ValueError('stored controller revisions differ from live evidence')
    result=_recheck(source,proposed,snapshot,('controller_executor','controller_grader'),
        'ahl.controller.recheck',owned.PROFILE,False)
    identity,=snapshot.metadata.identities
    observed=replace(identity,fields=tuple(replace(field,status='verified') if field.name in ('session','attempt','runtime_version','grader_revision')
        else field for field in identity.fields))
    data=connector.capture.read()
    executor=data['kernels'][data['grade']['executor_ordinal']]
    harness=executor['input_digests'].get('harness')
    if harness is not None:fingerprint(harness)
    result.update(profile=owned.PROFILE,verification_scope='closed_live_controller_attempt_only',publication_allowed=False,
        verified_execution=dict(**observed.to_dict(),profile=owned.PROFILE,publication_allowed=False),
        controller_harness_input=dict(schema_version=1,profile=owned.PROFILE,namespace='ahl.controller_checked_input_tree',
            status='observed' if harness is not None else 'unknown',sha256=harness,
            observation_scope='controller_input_checks_before_and_after_executor',
            effective_loading='unverified',publication_allowed=False),
        native_identity='unverified',task_outcomes_verified=False)
    return result


class SourceSink:
    def __init__(self, cache, project_sha256, scope_sha256, harness_capture=None):
        self.source=SourceSpec('controller','ahl-controller','v1',digest(('ahl.controller-source-v1',secrets.token_hex(32))),project_sha256,scope_sha256,
            identity_provenance='source_declared_id',enabled=True)
        self.scope=dict(project_sha256=project_sha256,scope_sha256=scope_sha256)
        self.store=SourceStore(cache,**self.scope)
        self.store._safe_path()
        # Optional opaque GitExport.harness_capture; any other value is reported as an unowned (unknown) binding.
        self.harness_capture=harness_capture

    def begin(self, artifacts, deadline):
        return owned.begin(self.source,artifacts,deadline)

    def finish(self, capture, inputs_unchanged):
        if type(capture) is not owned._Attempt:
            return dict(status='unknown',profile=owned.PROFILE,reason='owned_source_capture_required',publication_allowed=False)
        try:
            capture.close(inputs_unchanged)
            connector=ControllerCapture(self.source,capture)
            registry=Registry(); registry.register(Capability('controller','ahl-controller','v1',
                ('behavioral_event','grader_reference')),connector)
            preview=registry.preview(self.source,**self.scope)
            receipt=registry.ingest(self.source,self.store,**self.scope)
            if self.store.status(self.source)['source_snapshot_sha256']!=preview.sha256:
                raise ValueError('controller source checkpoint changed')
            live=registry.recheck(self.source,self.store,**self.scope)
            if self.harness_capture is not None:
                # Same live capability as the verified executor/grader link; private, never journaled.
                live['harness_snapshot_binding']=harness_binding.bind(capture,self.harness_capture)
            return dict(status='observed',profile=owned.PROFILE,publication_allowed=False,import_receipt=receipt.to_dict(),
                live_verification=live)
        except (ValueError,OSError,TypeError,KeyError):
            capture.cancel()
            return dict(status='unknown',profile=owned.PROFILE,reason='owned_source_capture_incomplete',publication_allowed=False)
