"""Číst nové commity dvou veřejných upstreamů nezávisle na řízení Remeha.

Referenční SHA označují skutečně převzatou historii tohoto vydání, nikoli poslední
kontrolu. Posunují se až při převzetí a ověření změn v novém vydání forku. Dotazy
na api.github.com jsou anonymní a neobsahují údaje domácnosti ani token Remeha.
Automatický interval je 12 h, ruční pokusy mají odstup nejméně 5 min. Výpadek,
limit API a přepsaná historie znamenají nedostupný počet, nikdy nulu změn.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
MIN_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class UpstreamSource:
    """Veřejný zdroj a poslední převzatý commit, bez místní konfigurace HA."""

    key: str
    repository: str
    baseline: str
    branch: str = "main"


SOURCES = (
    UpstreamSource("original", "msvisser/remeha_home", "691f0c5f12666c1f0b618e252ceb9255c0ec4690"),
    UpstreamSource(
        "community", "hugo-leij/remeha_home", "20bf0ffa82055c1b341ae538529af4473fbbb5bf"
    ),
)


class UpstreamCheckError(Exception):
    """Bezpečný kód chyby; odpověď GitHubu ani síťové výjimky neexportujeme."""

    def __init__(self, code: str, retry_at=None):
        super().__init__(code)
        self.retry_at = retry_at


class RemehaUpstreamCoordinator(DataUpdateCoordinator):
    """Samostatný čtecí koordinátor; GitHub neblokuje start či obnovu termostatu.

    Každý zdroj má vlastní výsledek, poslední úspěch a odklad podle limitu API.
    Souběžná stisknutí sdílí zámek; cache chrání anonymní limit GitHubu. První
    čtení běží na pozadí po načtení platforem. Unload odpojí posluchače a úlohu
    spravuje config entry. Po restartu se stav znovu ověří, nepředpokládá se nula.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="remeha_home upstream",
            update_interval=timedelta(hours=12),
        )
        self.session = async_get_clientsession(hass)
        self._lock = asyncio.Lock()
        self._attempted_at = None
        self._retry_at = {}
        self._last_success = {}

    async def _get_json(self, url: str) -> Any:
        """Provést GET na pevné veřejné URL; nepoužívat OAuth/HACS přihlášení.

        HTTP chyby nečteme jako úspěšná data. Pro 403/429 respektujeme Retry-After
        a X-RateLimit-Reset, nejvýše 24 h; bez čitelné hlavičky čekáme aspoň hodinu.
        Přesměrování se odmítá, aby změna adresy vyžadovala úpravu zdrojů forku.
        """
        async with self.session.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "remeha_home upstream checker",
            },
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                retry = None
                if response.status in (403, 429):
                    now = dt_util.utcnow()
                    seconds = 3600
                    for header, absolute in [("Retry-After", False), ("X-RateLimit-Reset", True)]:
                        try:
                            value = int(response.headers[header])
                            seconds = max(
                                seconds, value - int(now.timestamp()) if absolute else value
                            )
                        except (KeyError, ValueError, TypeError):
                            pass
                    retry = now + timedelta(seconds=min(seconds, 86400))
                raise UpstreamCheckError(f"http_{response.status}", retry)
            return await response.json()

    async def _check_source(self, source: UpstreamSource) -> dict:
        """Vrátit potvrzený počet commitů nebo samostatný chybný stav zdroje.

        Nejdříve se přečte hlavní větev a její neměnné SHA. Porovnání pak používá
        právě toto SHA, takže posun větve mezi dvěma GET nezkreslí počet/odkaz.
        Přepsaná či vrácená historie není bezpečně srovnatelná s převzatým základem.
        """
        now = dt_util.utcnow()
        result = {
            "repository": source.repository,
            "baseline": source.baseline,
            "branch": source.branch,
            "checked_at": now.isoformat(),
            "commits": None,
            "error": None,
            "compare_url": f"https://github.com/{source.repository}/compare/{source.baseline}...{source.branch}",
        }
        previous = self._last_success.get(source.key)
        if previous:
            result["last_success_at"] = previous["checked_at"]
            result["last_success_commits"] = previous["commits"]
        if self._retry_at.get(source.key, now) > now:
            return {
                **result,
                "error": "rate_limited",
                "checked_at": (self.data or {}).get(source.key, {}).get("checked_at"),
                "retry_at": self._retry_at[source.key].isoformat(),
            }
        try:
            async with asyncio.timeout(15):
                head = await self._get_json(
                    f"https://api.github.com/repos/{source.repository}/commits/{source.branch}"
                )
                sha = head["sha"]
                if (
                    not isinstance(sha, str)
                    or len(sha) != 40
                    or any(c not in "0123456789abcdef" for c in sha)
                ):
                    raise ValueError("Invalid head SHA")
                comparison = await self._get_json(
                    f"https://api.github.com/repos/{source.repository}/compare/{source.baseline}...{sha}?per_page=1"
                )
                if comparison["status"] not in ("identical", "ahead"):
                    raise UpstreamCheckError("history_changed")
                count = comparison["ahead_by"]
                if (
                    type(count) is not int
                    or count < 0
                    or comparison["status"] == "identical"
                    and count != 0
                ):
                    raise ValueError("Invalid commit count")
                result.update(
                    commits=count,
                    head_sha=sha,
                    compare_url=f"https://github.com/{source.repository}/compare/{source.baseline}...{sha}",
                    latest_commit_url=f"https://github.com/{source.repository}/commit/{sha}",
                    last_success_at=now.isoformat(),
                    last_success_commits=count,
                )
                self._last_success[source.key] = dict(result)
                self._retry_at.pop(source.key, None)
        except UpstreamCheckError as err:
            result["error"] = str(err)
            if err.retry_at:
                self._retry_at[source.key] = err.retry_at
                result["retry_at"] = err.retry_at.isoformat()
        except (TimeoutError, ClientError):
            result["error"] = "network_error"
        except (ValueError, TypeError, KeyError):
            result["error"] = "invalid_response"
        return result

    async def _async_update_data(self):
        """Obnovit oba zdroje souběžně do 15 s; cache omezí opakované stisknutí.

        Selhání jednoho zdroje neovlivní druhý. Čas pokusu i rate limit zůstávají
        oddělené od posledního úspěchu; návrat cache není vydáván za nové ověření.
        """
        async with self._lock:
            now = dt_util.utcnow()
            if (
                self._attempted_at is not None
                and now - self._attempted_at < MIN_INTERVAL
                and self.data is not None
            ):
                return self.data
            self._attempted_at = now
            rows = await asyncio.gather(*(self._check_source(source) for source in SOURCES))
            return {source.key: row for source, row in zip(SOURCES, rows, strict=True)}
