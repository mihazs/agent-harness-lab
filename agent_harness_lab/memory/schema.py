"""Versioned schema: forward-only migrations, enforced enums, JSON validity and per-project row ceilings."""
import sqlite3

ENUMS = {
    'artifact_kind': ('capture', 'analysis', 'report', 'evaluation', 'comparison', 'manifest'),
    'stage_status': ('complete', 'incomplete', 'blocked', 'unknown'),
    'outcome': ('pass', 'fail', 'inconclusive', 'blocked', 'unknown'),
    'metric_status': ('measured', 'unknown'),
    'comparison_status': ('comparable', 'not_comparable', 'blocked', 'unknown'),
    'finding_level': ('observed', 'supported', 'validated'),
    'link_target': ('artifact', 'evaluation', 'comparison'),
}
# Rows per project; a trigger refuses the next insert so one project cannot exhaust the index.
ROW_LIMITS = {'artifacts': 100_000, 'snapshots': 10_000, 'stages': 100_000, 'dependencies': 500_000,
              'evaluations': 100_000, 'metrics': 500_000, 'comparisons': 100_000, 'findings': 100_000,
              'finding_links': 500_000}
DETAIL = "TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid({c}) AND json_type({c})='object' AND length({c})<=4096)"


def _in(name):
    return 'IN (' + ','.join(repr(v) for v in ENUMS[name]) + ')'


def _detail(column='detail_json'):
    return column + ' ' + DETAIL.format(c=column)


def _v1():
    ident = "TEXT NOT NULL CHECK (length({c}) BETWEEN 1 AND 128 AND {c} NOT GLOB '*[^A-Za-z0-9._-]*')"
    digest = "TEXT NOT NULL CHECK (length({c})=64 AND {c} NOT GLOB '*[^0-9a-f]*')"
    pid, d = ident.format(c='project_id'), digest
    art = 'FOREIGN KEY (project_id, {c}) REFERENCES artifacts (project_id, digest) DEFERRABLE INITIALLY DEFERRED'
    statements = [
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
        f"CREATE TABLE projects (project_id {pid.replace('NOT NULL', 'PRIMARY KEY', 1)})",
        f"CREATE TABLE artifacts (project_id {pid}, digest {d.format(c='digest')},"
        f" record {d.format(c='record')}, kind TEXT NOT NULL CHECK (kind {_in('artifact_kind')}), size INTEGER NOT NULL CHECK (size >= 0),"
        " status TEXT NOT NULL DEFAULT 'published' CHECK (status = 'published'),"
        " PRIMARY KEY (project_id, digest), FOREIGN KEY (project_id) REFERENCES projects (project_id) DEFERRABLE INITIALLY DEFERRED) WITHOUT ROWID",
        f"CREATE TABLE dependencies (project_id {pid}, dependent {d.format(c='dependent')},"
        f" dependency {d.format(c='dependency')}, CHECK (dependent <> dependency),"
        f" PRIMARY KEY (project_id, dependent, dependency), {art.format(c='dependent')}, {art.format(c='dependency')}) WITHOUT ROWID",
        f"CREATE TABLE snapshots (project_id TEXT NOT NULL, snapshot_id {ident.format(c='snapshot_id')}, manifest {d.format(c='manifest')},"
        f" PRIMARY KEY (project_id, snapshot_id), {art.format(c='manifest')}) WITHOUT ROWID",
        f"CREATE TABLE stages (project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, stage {ident.format(c='stage')},"
        f" algorithm_version {ident.format(c='algorithm_version')}, output TEXT CHECK (output IS NULL OR (length(output)=64 AND output NOT GLOB '*[^0-9a-f]*')),"
        f" status TEXT NOT NULL CHECK (status {_in('stage_status')}), CHECK (status <> 'complete' OR output IS NOT NULL),"
        f" PRIMARY KEY (project_id, snapshot_id, stage), FOREIGN KEY (project_id, snapshot_id) REFERENCES snapshots (project_id, snapshot_id) DEFERRABLE INITIALLY DEFERRED,"
        " FOREIGN KEY (project_id, output) REFERENCES artifacts (project_id, digest) DEFERRABLE INITIALLY DEFERRED) WITHOUT ROWID",
        # An index row never proves that an external original HistoryStore was updated: only 'unverified' is storable.
        f"CREATE TABLE evaluations (project_id TEXT NOT NULL, evaluation_id {ident.format(c='evaluation_id')}, result {d.format(c='result')},"
        f" outcome TEXT NOT NULL CHECK (outcome {_in('outcome')}), external_ref TEXT CHECK (external_ref IS NULL OR length(external_ref) <= 256),"
        f" external_state TEXT NOT NULL DEFAULT 'unverified' CHECK (external_state = 'unverified'), {_detail()},"
        f" PRIMARY KEY (project_id, evaluation_id), {art.format(c='result')}) WITHOUT ROWID",
        f"CREATE TABLE metrics (project_id TEXT NOT NULL, evaluation_id TEXT NOT NULL, name {ident.format(c='name')}, value REAL,"
        f" status TEXT NOT NULL CHECK (status {_in('metric_status')}), CHECK ((status = 'measured') = (value IS NOT NULL)),"
        " PRIMARY KEY (project_id, evaluation_id, name),"
        " FOREIGN KEY (project_id, evaluation_id) REFERENCES evaluations (project_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED) WITHOUT ROWID",
        f"CREATE TABLE comparisons (project_id TEXT NOT NULL, comparison_id {ident.format(c='comparison_id')}, baseline TEXT NOT NULL,"
        f" candidate TEXT NOT NULL, status TEXT NOT NULL CHECK (status {_in('comparison_status')}), {_detail()}, CHECK (baseline <> candidate),"
        " PRIMARY KEY (project_id, comparison_id),"
        " FOREIGN KEY (project_id, baseline) REFERENCES evaluations (project_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED,"
        " FOREIGN KEY (project_id, candidate) REFERENCES evaluations (project_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED) WITHOUT ROWID",
        f"CREATE TABLE findings (project_id TEXT NOT NULL, finding_id {ident.format(c='finding_id')}, evidence {d.format(c='evidence')},"
        f" level TEXT NOT NULL CHECK (level {_in('finding_level')}), {_detail()},"
        f" PRIMARY KEY (project_id, finding_id), {art.format(c='evidence')}) WITHOUT ROWID",
        f"CREATE TABLE finding_links (project_id TEXT NOT NULL, finding_id TEXT NOT NULL, target_type TEXT NOT NULL CHECK (target_type {_in('link_target')}),"
        f" target_id {ident.format(c='target_id')}, PRIMARY KEY (project_id, finding_id, target_type, target_id),"
        " FOREIGN KEY (project_id, finding_id) REFERENCES findings (project_id, finding_id) DEFERRABLE INITIALLY DEFERRED) WITHOUT ROWID",
        "CREATE INDEX stages_by_status ON stages (project_id, status)",
        "CREATE INDEX evaluations_by_outcome ON evaluations (project_id, outcome)",
        "CREATE INDEX findings_by_level ON findings (project_id, level)",
        "CREATE INDEX dependencies_reverse ON dependencies (project_id, dependency)",
    ]
    for table, limit in ROW_LIMITS.items():
        statements.append(
            f"CREATE TRIGGER {table}_ceiling BEFORE INSERT ON {table} WHEN (SELECT count(*) FROM {table} WHERE project_id = NEW.project_id) >= {limit} "
            f"BEGIN SELECT RAISE(ABORT, '{table} row ceiling reached for this project'); END")
    return statements


# Append-only: a released step is never edited. Each step takes the schema from user_version N-1 to N.
MIGRATIONS = (_v1(),)
VERSION = len(MIGRATIONS)


def migrate(connection, migrations=MIGRATIONS):
    """Apply every missing step in order, each in its own transaction; refuses a newer database."""
    current = connection.execute('PRAGMA user_version').fetchone()[0]
    if current > len(migrations):
        raise ValueError(f'index schema {current} is newer than supported {len(migrations)}')
    for number, statements in enumerate(migrations[current:], current + 1):
        connection.execute('BEGIN IMMEDIATE')
        try:
            for statement in statements:
                connection.execute(statement)
            connection.execute(f'PRAGMA user_version = {number}')
            connection.execute('COMMIT')
        except sqlite3.Error:
            connection.execute('ROLLBACK')
            raise
    return len(migrations)
