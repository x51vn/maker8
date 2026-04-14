"""Read-only credential reader for editor8's ``service_keys`` / ``tts_presets`` tables.

This module is used when ``credential_source == "db"`` in :class:`~maker8.config.Settings`.
It connects directly (read-only) to editor8's PostgreSQL database via psycopg2 and
provides a time-based cache so repeated lookups within the same job do not hit the DB.

Design invariants
~~~~~~~~~~~~~~~~~
* **Read-only** – maker8 never writes to editor8's DB.
* **Fail-soft on refresh** – if the DB is temporarily unreachable the stale
  cache is kept so in-progress jobs are not interrupted.  The first call
  (empty cache) *will* propagate errors so startup fail-fast can detect them.
* **Thread-safe** – a single lock guards both the cache dict and the timestamp.

Credential format by ``provider_type``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* ``google_cloud_service_account``: ``secret_value`` contains the **raw JSON
  content** of a Google Cloud service-account key file (not a file path).
* ``elevenlabs_api_key``: ``secret_value`` is the plain-text ElevenLabs API key.
* ``dropbox_oauth``: ``secret_value`` is a JSON object with keys
  ``app_key``, ``app_secret``, ``refresh_token``.
* All other types: ``secret_value`` is the raw credential string.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

try:
    import psycopg2
    import psycopg2.extras

    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG2_AVAILABLE = False

from maker8.utils.logging import get_logger

log = get_logger(__name__)

# SQL executed against editor8's DB – uses only stable columns from 013 migration.
_KEYS_SQL = """
SELECT provider_type, secret_value
FROM service_keys
WHERE status = 'active'
ORDER BY priority DESC, created_at ASC
"""

_PRESETS_SQL = """
SELECT preset_ref, provider_type, config_json
FROM tts_presets
WHERE status = 'active'
"""


class CredentialReader:
    """Read active credentials from editor8's ``service_keys`` table.

    Parameters
    ----------
    database_url:
        libpq-compatible connection string, e.g.
        ``postgresql://user:pass@host:5432/editor8``.
    ttl_sec:
        How many seconds to keep the in-memory cache before re-querying.
        Defaults to 60 seconds.
    """

    def __init__(self, database_url: str, ttl_sec: float = 60.0) -> None:
        if not _PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "psycopg2-binary is required for credential_source='db'. "
                "Install it with: pip install psycopg2-binary"
            )
        self._database_url = database_url
        self._ttl_sec = ttl_sec
        self._cache: dict[str, list[str]] = {}  # provider_type → [secret_value, ...]
        self._preset_cache: dict[str, dict[str, Any]] = {}  # preset_ref → config_json
        self._cache_time: float = 0.0
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def get_keys(self, provider_type: str) -> list[str]:
        """Return all active ``secret_value`` strings for *provider_type*.

        Keys are returned in priority-descending order (highest first).
        Returns an empty list if no active keys exist.
        """
        self._maybe_refresh()
        return list(self._cache.get(provider_type, []))

    def get_first_key(self, provider_type: str) -> str | None:
        """Return the highest-priority active key value or ``None``."""
        keys = self.get_keys(provider_type)
        return keys[0] if keys else None

    def get_first_key_json(self, provider_type: str) -> dict[str, Any] | None:
        """Return the first active key parsed as JSON, or ``None``.

        Useful for ``dropbox_oauth`` whose ``secret_value`` is a JSON object.
        """
        raw = self.get_first_key(provider_type)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error(
                "credential_reader.json_parse_failed",
                provider_type=provider_type,
                error=str(exc),
            )
            return None

    def has_keys(self, provider_type: str) -> bool:
        """Return ``True`` if at least one active key exists for *provider_type*."""
        return bool(self.get_keys(provider_type))

    def get_tts_preset(self, preset_ref: str) -> dict[str, Any] | None:
        """Return the ``config_json`` for a TTS preset, or ``None`` if not found."""
        self._maybe_refresh()
        return self._preset_cache.get(preset_ref)

    def get_all_tts_presets(self) -> dict[str, dict[str, Any]]:
        """Return a mapping of preset_ref → config_json for all active presets."""
        self._maybe_refresh()
        return dict(self._preset_cache)

    def readiness_check(self) -> list[str]:
        """Return a list of missing-credential error strings (empty = OK).

        Checks for credentials required by maker8:
        * ``dropbox_oauth``
        * At least one TTS key (``google_cloud_service_account`` or
          ``elevenlabs_api_key``) OR the default ``gtts`` provider which
          needs no key.
        """
        errors: list[str] = []
        if not self.has_keys("dropbox_oauth"):
            errors.append(
                "No active 'dropbox_oauth' key found in editor8 DB. "
                "Add one via the editor8 UI → Settings → API Keys."
            )
        return errors

    # ── Internal ──────────────────────────────────────────────────────

    def _maybe_refresh(self) -> None:
        with self._lock:
            if time.time() - self._cache_time < self._ttl_sec:
                return
            self._refresh_locked()

    def _refresh_locked(self) -> None:
        """Re-query the DB and repopulate the caches (must hold ``_lock``)."""
        try:
            conn = psycopg2.connect(self._database_url, connect_timeout=10)
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(_KEYS_SQL)
                    key_rows = cur.fetchall()

                    cur.execute(_PRESETS_SQL)
                    preset_rows = cur.fetchall()
            finally:
                conn.close()

            new_cache: dict[str, list[str]] = {}
            for row in key_rows:
                pt: str = row["provider_type"]
                sv: str = row["secret_value"]
                new_cache.setdefault(pt, []).append(sv)

            new_presets: dict[str, dict[str, Any]] = {}
            for row in preset_rows:
                ref: str = row["preset_ref"]
                cfg = row["config_json"]
                # JSONB columns come back as dicts from psycopg2
                new_presets[ref] = cfg if isinstance(cfg, dict) else json.loads(cfg)

            self._cache = new_cache
            self._preset_cache = new_presets
            self._cache_time = time.time()
            log.debug(
                "credential_reader.cache_refreshed",
                provider_types=list(new_cache.keys()),
                total_keys=sum(len(v) for v in new_cache.values()),
                total_presets=len(new_presets),
            )
        except Exception as exc:
            log.error(
                "credential_reader.refresh_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                hint="Credentials will use stale cache (or raise if first load).",
            )
            # If we have never successfully loaded, propagate so startup can fail-fast.
            if self._cache_time == 0.0:
                raise
