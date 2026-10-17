"""Kontrola commitů: skutečný koordinátor HA, pouze veřejné HTTP je simulované."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import homeassistant.util.dt as dt_util
import pytest
from aiohttp import ClientConnectionError

from custom_components.remeha_home.button import RemehaUpstreamCheckButton
from custom_components.remeha_home.sensor import RemehaUpstreamSensor
from custom_components.remeha_home.upstream import SOURCES, RemehaUpstreamCoordinator


class Response:
    """Malá náhrada HTTP kontextu, bez připojení k síti či tokenů."""

    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status, self.headers = data, status, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.data


@pytest.fixture
def github(hass, ready):
    """Oba upstreamy jsou na známém převzatém základu; HTTP kontrolujeme přes URL."""
    _, entry = ready
    coordinator = RemehaUpstreamCoordinator(hass, entry)
    replies = {}
    for source in SOURCES:
        prefix = f"https://api.github.com/repos/{source.repository}"
        replies[f"{prefix}/commits/main"] = Response({"sha": source.baseline})
        replies[f"{prefix}/compare/{source.baseline}...{source.baseline}?per_page=1"] = Response(
            {"status": "identical", "ahead_by": 0}
        )
    coordinator.session = Mock()
    coordinator.session.get.side_effect = lambda url, **kwargs: replies[url]
    return coordinator, replies


async def test_known_zero_cache_and_no_credentials(github):
    """Potvrzená nula je platná, okamžité opakování nesmí utrácet další požadavky."""
    c, _ = github
    await c.async_refresh()
    assert c.data["original"]["commits"] == c.data["community"]["commits"] == 0
    stamp = c.data["original"]["checked_at"]
    await c.async_refresh()
    assert c.session.get.call_count == 4
    assert c.data["original"]["checked_at"] == stamp
    for call in c.session.get.call_args_list:
        assert "Authorization" not in call.kwargs["headers"]
        assert call.kwargs["allow_redirects"] is False


async def test_new_commits_compare_exact_head(github, ready):
    """I velký počet změn je ahead_by, nikoli délka první stránky seznamu."""
    c, replies = github
    source = SOURCES[1]
    sha = "a" * 40
    prefix = f"https://api.github.com/repos/{source.repository}"
    replies[f"{prefix}/commits/main"] = Response({"sha": sha})
    replies[f"{prefix}/compare/{source.baseline}...{sha}?per_page=1"] = Response(
        {"status": "ahead", "ahead_by": 301}
    )
    await c.async_refresh()
    item = RemehaUpstreamSensor(c, ready[1], source, {})
    assert item.available and item.native_value == 301
    assert item.extra_state_attributes["compare_url"].endswith(sha)
    assert c.data["original"]["commits"] == 0


@pytest.mark.parametrize(
    "status,count",
    [("diverged", 4), ("behind", 0), ("ahead", True), ("ahead", -1), ("identical", 2)],
)
async def test_history_or_invalid_count_is_not_zero(github, ready, status, count):
    """Přepsaná historie, bool a neplatný počet nejsou potvrzeným stavem bez změn."""
    c, replies = github
    source = SOURCES[0]
    url = f"https://api.github.com/repos/{source.repository}/compare/{source.baseline}...{source.baseline}?per_page=1"
    replies[url] = Response({"status": status, "ahead_by": count})
    await c.async_refresh()
    item = RemehaUpstreamSensor(c, ready[1], source, {})
    assert not item.available
    assert item.native_value is None
    assert c.data["community"]["commits"] == 0


async def test_rate_limit_preserves_last_success_and_other_source(github, ready):
    """403 znepřístupní jen daný zdroj a respektuje odklad i při ruční kontrole."""
    c, replies = github
    await c.async_refresh()
    c._attempted_at -= timedelta(minutes=6)
    source = SOURCES[0]
    url = f"https://api.github.com/repos/{source.repository}/commits/main"
    replies[url] = Response(status=403, headers={"Retry-After": "7200"})
    await c.async_refresh()
    item = RemehaUpstreamSensor(c, ready[1], source, {})
    assert not item.available and item.native_value is None
    assert item.extra_state_attributes["last_success_commits"] == 0
    assert item.extra_state_attributes["error"] == "http_403"
    assert c.data["community"]["commits"] == 0
    retry = dt_util.parse_datetime(item.extra_state_attributes["retry_at"])
    assert retry > dt_util.utcnow() + timedelta(minutes=119)
    calls = c.session.get.call_count
    c._attempted_at -= timedelta(minutes=6)
    await c.async_refresh()
    assert c.session.get.call_count == calls + 2  # Obnoví se pouze komunitní zdroj.
    assert c.data["original"]["error"] == "rate_limited"


async def test_network_failure_button_can_retry(github, ready):
    """Selhání sítě nepředstírá nulu a nevyřadí tlačítko dalšího ověření."""
    c, _ = github
    c.session.get.side_effect = ClientConnectionError()
    await c.async_refresh()
    item = RemehaUpstreamSensor(c, ready[1], SOURCES[0], {})
    assert not item.available and item.native_value is None
    assert item.extra_state_attributes["error"] == "network_error"
    c.async_request_refresh = AsyncMock()
    button = RemehaUpstreamCheckButton(c, ready[1], {})
    assert button.available
    await button.async_press()
    c.async_request_refresh.assert_awaited_once()
