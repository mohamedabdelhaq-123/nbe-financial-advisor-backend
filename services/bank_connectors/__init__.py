"""
get_connector(provider_slug) is the only place any call site needs to know
which bank/adapter it's talking to — see BankConnector's docstring
(services/bank_connectors/base.py) for why the interface is shaped the way
it is.
"""

from .base import BankConnector, BankConnectorError
from .mock_bank import MockBankConnector

_REGISTRY: dict[str, type[BankConnector]] = {
    "mock_bank": MockBankConnector,
}


def get_connector(provider_slug: str) -> BankConnector:
    try:
        return _REGISTRY[provider_slug]()
    except KeyError as exc:
        raise BankConnectorError(f"No bank connector registered for '{provider_slug}'.") from exc


__all__ = ["BankConnector", "BankConnectorError", "get_connector"]
