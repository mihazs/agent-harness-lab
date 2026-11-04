"""Explicit read-only analysis admission; never a worker output or authority policy."""
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from .integrity import digest

UNLIMITED=dict(max_files=None,max_bytes=None,max_entries=None,max_depth=None)


@dataclass(frozen=True)
class AnalysisInputs(Mapping):
    harness: Path
    fixture: Path

    def __post_init__(self):
        object.__setattr__(self,'harness',Path(self.harness))
        object.__setattr__(self,'fixture',Path(self.fixture))

    def __getitem__(self,name):
        if name not in ('harness','fixture'):raise KeyError(name)
        return getattr(self,name)

    def __iter__(self):return iter(('harness','fixture'))
    def __len__(self):return 2


def check_role(inputs,role):
    if type(inputs) is AnalysisInputs and role not in ('proposer','evaluator_author'):
        raise ValueError('analysis input admission is restricted to preparation roles')


def input_digest(inputs,name):
    return digest(inputs[name],**(UNLIMITED if type(inputs) is AnalysisInputs and name=='fixture' else {}))
