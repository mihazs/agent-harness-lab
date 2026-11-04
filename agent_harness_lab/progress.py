"""Coarse aggregate progress on stderr; stdout remains the workflow JSON receipt."""
import sys
import time


class Progress:
    def __init__(self, stream=None, clock=time.monotonic):
        self.stream = sys.stderr if stream is None else stream
        self.clock = clock
        self.started = self.clock()
        self.last = self.started
        self.stage = None

    def __call__(self, stage, **counts):
        if stage not in ('discovery', 'capture_selection', 'capture_processing', 'collection',
                         'analysis', 'evaluation_reports', 'evidence_empty', 'evidence_incomplete',
                         'collection_complete', 'complete', 'memory_collection', 'memory_readback',
                         'candidate_preparation', 'evaluation'):
            raise ValueError('unsupported progress stage')
        if any(key not in ('files', 'scanned', 'selected', 'capture', 'captures', 'events',
                           'eligible', 'findings', 'edges', 'evaluation_outcomes', 'incomplete',
                           'pages', 'observations', 'attempt')
               or type(value) is not int or value < 0 for key, value in counts.items()):
            raise ValueError('progress accepts aggregate counts only')
        now = self.clock()
        if stage != self.stage or now - self.last >= 1:
            values = ', '.join(f'{key}={value}' for key, value in counts.items())
            print(f'ahl: {stage} ({now - self.started:.1f}s)' + (f': {values}' if values else ''),
                  file=self.stream, flush=True)
            self.stage, self.last = stage, now
