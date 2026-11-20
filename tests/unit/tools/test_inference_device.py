"""The optional local-model benchmark sends inference to an accelerator when torch reports one."""
import sys
from types import SimpleNamespace

import pytest

from tools.benchmark_models import inference_device


@pytest.mark.parametrize('cuda, mps, override, expected', [
    (True, True, None, 'cuda'), (False, True, None, 'mps'), (False, False, None, 'cpu'), (True, True, 'cpu', 'cpu')])
def test_an_available_accelerator_is_used_unless_overridden(monkeypatch, cuda, mps, override, expected):
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
                           backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)))
    monkeypatch.setitem(sys.modules, 'torch', fake)
    monkeypatch.delenv('AHL_DEVICE', raising=False)
    if override: monkeypatch.setenv('AHL_DEVICE', override)
    assert inference_device() == expected
