"""Explicit GitHub configuration; probes never read credentials or contact GitHub."""
from .github import GitHubAPI, GitHubSelection
from .remote_config import ConfiguredAPI


class GitHubConfigured(ConfiguredAPI):
    provider, selection_type, reader = 'github', GitHubSelection, GitHubAPI
    reconciles_absence = True

    def __init__(self, manifest, *, authorization_file=None):
        super().__init__(manifest, authorization_file=authorization_file)
