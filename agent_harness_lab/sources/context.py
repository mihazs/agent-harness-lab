"""Explicit offline journal context; directory names do not bind source identity."""
def load(cache=None, project_sha256=None, scope_sha256=None):
    selected = (cache, project_sha256, scope_sha256)
    if all(value is None for value in selected): return None
    if any(value is None for value in selected):
        raise ValueError('source context requires a private cache and expected project/scope fingerprints')
    from ..config import supported
    if not supported(): raise NotImplementedError('source journal inspection requires safe filesystem APIs')
    from .store import SourceStore
    try:
        return SourceStore(cache, project_sha256=project_sha256, scope_sha256=scope_sha256).inventory()
    except PermissionError:
        raise PermissionError('source context unavailable; inspect private journal access') from None
    except (ValueError, OSError, TypeError, KeyError):
        raise ValueError('source context rejected; inspect expected scope and private journal') from None
