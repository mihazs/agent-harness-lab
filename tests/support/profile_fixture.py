"""Synthetic checkouts for the project-profile tests: a dict of relative path to text under tmp_path."""
import json
from pathlib import Path


def build(tmp_path, files, name='project'):
    root = Path(tmp_path) / name
    root.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(text) if isinstance(text, dict) else text)
    return root


def package(scripts=None, **extra):
    return dict(name='synthetic', scripts=scripts or {}, **extra)
