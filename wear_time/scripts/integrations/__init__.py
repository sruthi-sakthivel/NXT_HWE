"""Minimal integrations package for the wear-time dashboard.

Only the Mixpanel client (and its BaseClient dependency) are needed here,
so this init avoids importing the other data-source clients that live in
the full eng-dashboard repo.
"""

from .base_client import BaseClient
from .mixpanel_client import MixpanelClient

__all__ = ["BaseClient", "MixpanelClient"]
