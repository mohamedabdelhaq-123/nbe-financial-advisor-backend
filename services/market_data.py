"""Provider-neutral quote client for holdings; sends no user-owned data."""

import datetime
import json
import uuid
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import InvestmentInstrument

_session = requests.Session()


class MarketDataUnavailable(RuntimeError):
    pass


def _cache_key(instruments: list[InvestmentInstrument]) -> str:
    ids = ":".join(sorted(str(item.id) for item in instruments))
    return f"holding-quotes:v1:{ids}"


def _parse_positive_decimal(value) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _validate_quotes(payload, instruments: list[InvestmentInstrument]) -> dict[uuid.UUID, dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("quotes"), list):
        raise MarketDataUnavailable("The market quote response was invalid.")

    expected = {item.id: item for item in instruments}
    validated: dict[uuid.UUID, dict] = {}
    now = timezone.now()
    for raw in payload["quotes"]:
        if not isinstance(raw, dict):
            continue
        try:
            instrument_id = uuid.UUID(str(raw.get("instrument_id")))
        except (TypeError, ValueError):
            continue
        instrument = expected.get(instrument_id)
        if instrument is None or instrument_id in validated:
            continue
        price = _parse_positive_decimal(raw.get("price"))
        observed_at = parse_datetime(str(raw.get("observed_at") or ""))
        source = raw.get("source")
        if (
            price is None
            or observed_at is None
            or not timezone.is_aware(observed_at)
            or observed_at > now + datetime.timedelta(minutes=5)
            or raw.get("price_currency", "").upper() != instrument.price_currency
            or raw.get("unit") != instrument.unit
            or raw.get("price_type") != instrument.price_type
            or not isinstance(source, str)
            or not source.strip()
            or len(source) > 120
        ):
            continue
        validated[instrument_id] = {
            "price": price,
            "price_currency": instrument.price_currency,
            "unit": instrument.unit,
            "price_type": instrument.price_type,
            "observed_at": observed_at,
            "source": source.strip(),
        }
    return validated


def fetch_market_quotes(instruments: list[InvestmentInstrument]) -> dict[uuid.UUID, dict]:
    """Fetch normalized quotes. Quantities, costs, and user identity never leave Django."""
    if not settings.MARKET_DATA_ENABLED:
        return {}
    if not instruments:
        return {}

    key = _cache_key(instruments)
    cached = cache.get(key)
    if cached is not None:
        return _validate_quotes(cached, instruments)

    headers = {"Accept": "application/json"}
    if settings.MARKET_DATA_API_KEY:
        headers["Authorization"] = f"Bearer {settings.MARKET_DATA_API_KEY}"
    request_body = {
        "instruments": [
            {
                "instrument_id": str(item.id),
                "code": item.code,
                "provider_symbol": item.provider_symbol,
                "asset_class": item.asset_class,
            }
            for item in instruments
        ]
    }
    try:
        response = _session.post(
            f"{settings.MARKET_DATA_BASE_URL}/v1/quotes",
            json=request_body,
            headers=headers,
            timeout=settings.MARKET_DATA_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        raise MarketDataUnavailable("Live prices are temporarily unavailable.") from exc

    validated = _validate_quotes(payload, instruments)
    cache.set(key, payload, timeout=settings.MARKET_DATA_CACHE_TTL_SECONDS)
    return validated
