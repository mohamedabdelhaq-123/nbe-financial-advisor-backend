"""
get_client() is the only place any call site needs to know whether it's
talking to the real AI Service or the in-process mock — see
BaseAIServiceClient's docstring (services/ai_service/base.py) for why the
interface is shaped the way it is.
"""

from django.conf import settings

from .ai_client import AIServiceClient
from .base import AIServiceError, BaseAIServiceClient
from .mock_client import MockAIServiceClient


def get_client() -> BaseAIServiceClient:
    """Reads settings.USE_MOCK_AI_SERVICE at call time (not import time), so
    override_settings works in tests — same reasoning as core/ask_view.py's
    commit 83bbf63. Constructing a fresh instance per call is cheap (no state
    to share), matching services/bank_connectors/__init__.py's
    get_connector()."""
    if settings.USE_MOCK_AI_SERVICE:
        return MockAIServiceClient()
    return AIServiceClient()


__all__ = [
    "BaseAIServiceClient",
    "AIServiceClient",
    "MockAIServiceClient",
    "AIServiceError",
    "get_client",
]
