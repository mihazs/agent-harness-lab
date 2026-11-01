"""Read explicitly authorized extracted ai-memory OKF v0.2 project bundles."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from agent_harness_lab.evaluation.integrity import contained, read_regular
from .schema import EvidenceRecord, digest
from .snapshot import LocalExport, Snapshot


class MissingOKFDependency(NotImplementedError):
    pass


def concept(raw):
    try:
        import yaml
    except ImportError:
        raise MissingOKFDependency('install the optional OKF extra') from None
    text = raw.decode('utf-8')
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\r\n') != '---':
        raise ValueError('OKF frontmatter is required')
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip('\r\n') == '---'), None)
    if end is None:
        raise ValueError('unterminated OKF frontmatter')
    header = ''.join(lines[1:end])
    if len(header.encode()) > 16384:
        raise ValueError('OKF metadata limit exceeded')
    try:
        if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for t in yaml.scan(header)):
            raise ValueError('YAML aliases are unsupported')
        class Loader(yaml.SafeLoader):
            pass
        def mapping(loader, node):
            pairs = loader.construct_pairs(node, deep=True)
            if any(not isinstance(k, str) for k, _ in pairs) or len(dict(pairs)) != len(pairs):
                raise ValueError('duplicate or non-text metadata keys')
            return dict(pairs)
        Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        metadata = yaml.load(header, Loader=Loader)
    except (yaml.YAMLError, TypeError, RecursionError):
        raise ValueError('unsupported OKF YAML') from None
    if not isinstance(metadata, dict):
        raise ValueError('OKF metadata must be an object')
    return metadata, ''.join(lines[end+1:])


def instant(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError('invalid OKF timestamp') from None
    else:
        raise ValueError('OKF timestamp must include an offset')
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('OKF timestamp must include an offset')
    return result


@dataclass(frozen=True)
class Page:
    path: str
    sha256: str
    metadata: dict
    body: str

    def freshness(self, now):
        value = self.metadata.get('stale_after')
        return 'unknown' if value is None else 'stale' if instant(value) <= now else 'current'

    @property
    def authorship(self):
        generated = self.metadata.get('generated', {})
        actor = generated.get('by') if isinstance(generated, dict) else None
        return 'unknown' if not isinstance(actor, str) or not actor else 'human_attributed' if actor.startswith('human:') else 'generated'


class OKFExport(LocalExport):
    def pages(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', 'ai-memory-okf', '0.2'):
            raise ValueError('unsupported ai-memory export version')
        current, value = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not source.enabled:
            raise ValueError('source declaration changed or is disabled')
        root = contained(self.manifest.parent, value['data'])
        meta, _ = concept(read_regular(contained(root, '_meta.md'), 65536))
        if not isinstance(meta.get('project'), str) or digest(meta['project']) != source.project_sha256:
            raise ValueError('ai-memory project identity mismatch')
        index, _ = concept(read_regular(contained(root, 'index.md'), 65536))
        if index.get('okf_version') != '0.2':
            raise ValueError('unsupported OKF version')
        selected, total, scanned = [], 0, 0
        for directory, folders, files in os.walk(root, followlinks=False):
            if Path(directory) != root and '_meta.md' in files:
                raise ValueError('nested scope manifests are unsupported')
            folders[:] = sorted(f for f in folders if f not in ('.git', '_pending'))
            scanned += len(folders)+len(files)
            if scanned > 2000 or any((Path(directory)/f).is_symlink() for f in folders):
                raise ValueError('unsafe or excessive OKF directory tree')
            for name in sorted(files):
                if not name.endswith('.md') or name in ('index.md', '_meta.md', 'log.md') or re.fullmatch(r'log-\d{4}-\d{2}\.md', name):
                    continue
                path = Path(directory)/name
                relative = str(path.relative_to(root))
                if name.startswith('.'):
                    raise ValueError('hidden concepts are unsupported')
                raw = read_regular(contained(root, relative), 256*1024)
                total += len(raw)
                if total > 4*1024*1024 or len(selected) >= 500:
                    raise ValueError('OKF bundle limits exceeded')
                metadata, body = concept(raw)
                if not isinstance(metadata.get('type'), str) or not metadata['type'].strip():
                    raise ValueError('OKF concept type is required')
                selected.append(Page(relative, hashlib.sha256(raw).hexdigest(), metadata, body))
        return tuple(selected)

    def read(self, source):
        pages = self.pages(source)
        now = datetime.now(timezone.utc)
        summary = dict(generated=0, human_attributed=0, unknown=0, stale=0, current=0,
                       unknown_freshness=0, deprecated=0, superseded=0, reviewed_claims=0)
        records = []
        for page in pages:
            summary[page.authorship] += 1
            freshness = page.freshness(now)
            summary['unknown_freshness' if freshness == 'unknown' else freshness] += 1
            if page.metadata.get('status') in ('deprecated', 'superseded'):
                summary[page.metadata['status']] += 1
            summary['reviewed_claims'] += page.metadata.get('review_status') == 'reviewed'
            generated = page.metadata.get('generated', {})
            at = generated.get('at') if isinstance(generated, dict) else None
            original = instant(at).isoformat() if at is not None else None
            records.append(EvidenceRecord(source.source_key, digest(page.path), page.sha256, page.sha256,
                'contextual_assertion', 'memory_page', now.isoformat(), original_at=original))
        self.summary = summary
        return Snapshot(digest([(p.path, p.sha256) for p in pages]), tuple(records))
