"""Target-state receipt for a committed journal record (#77). Re-reads the installed files; asserts no behavior."""
from .promotion.journal import file_record


def application_receipt(journal, record):
    """Compare current project bytes and modes with the record's 'after' membership, per path.
    A file copy is not proof of improvement or of hook execution, so both are reported as not established."""
    operations, verified = [], record['status'] == 'committed' and journal.active == record['deployment']
    for name in sorted(record['before'].keys() | record['after'].keys()):
        before, after = record['before'].get(name), record['after'].get(name)
        try:
            matches = file_record(journal.project / name) == after
        except (ValueError, OSError):
            matches = False
        verified &= matches
        operations.append(dict(path=name, action='create' if before is None else 'remove' if after is None else 'update',
            sha256=after and after['sha256'], mode=after and after['mode'], verified=matches))
    rollback = journal.preview_rollback(record['deployment'])
    return dict(status='applied_verified' if verified else 'applied_unverified', deployment=record['deployment'],
        operations=operations, rollback=rollback['status'], activation='unknown', behavioral_effect='not_established')
