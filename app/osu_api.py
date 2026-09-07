"""Thin osu! API v2 client, ported from Relaxation Vault's OsuApiProvider.cs."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("ctbrx.osu_api")

OSU_BASE = "https://osu.ppy.sh"
TOKEN_URL = f"{OSU_BASE}/oauth/token"
API = f"{OSU_BASE}/api/v2"


class OsuApiError(RuntimeError):
    pass


class OsuApiClient:
    def __init__(self, client_id: int, client_secret: str, interval: float = 0.75):
        self.client_id = client_id
        self.client_secret = client_secret
        self.interval = interval
        self._session = requests.Session()
        self._session.headers.update({"x-api-version": "99999999", "Accept": "application/json"})
        self._token: str | None = None
        self._token_expiry = datetime.min.replace(tzinfo=timezone.utc)
        self._last_call = 0.0

    # ---- auth -----------------------------------------------------------------
    def _ensure_token(self) -> None:
        if self._token and datetime.now(timezone.utc) < self._token_expiry:
            return
        resp = requests.post(
            TOKEN_URL,
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": "public",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if not resp.ok:
            raise OsuApiError(f"token request failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = datetime.now(timezone.utc) + timedelta(
            seconds=int(data.get("expires_in", 3600)) - 60
        )
        self._session.headers["Authorization"] = f"Bearer {self._token}"

    # ---- low-level ----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path: str, *, params=None, allow_404=True):
        self._ensure_token()
        for attempt in range(6):
            self._throttle()
            try:
                resp = self._session.get(f"{API}{path}", params=params, timeout=30)
            except requests.exceptions.RequestException as exc:
                log.warning("GET %s network error (%s), retrying", path, exc)
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code == 401:
                self._token = None
                self._ensure_token()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if not resp.ok:
                raise OsuApiError(f"GET {path} -> {resp.status_code} {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError:
                time.sleep(3 * (attempt + 1))
                continue
        raise OsuApiError(f"GET {path} failed after retries")

    # ---- endpoints --------------------------------------------------------
    def get_scores(self, cursor_string: str | None = None) -> dict | None:
        params = {"cursor_string": cursor_string} if cursor_string else None
        return self._get("/scores", params=params, allow_404=False)

    def get_user_scores(self, user_id: int, kind: str = "recent", limit: int = 50) -> list[dict]:
        """kind: 'recent' | 'best'. Catch only, passed scores only."""
        data = self._get(
            f"/users/{user_id}/scores/{kind}",
            params={"mode": "fruits", "limit": limit},
            allow_404=True,
        )
        return data or []

    def get_beatmap(self, beatmap_id: int) -> dict | None:
        return self._get(f"/beatmaps/{beatmap_id}")

    def get_beatmap_scores(self, beatmap_id: int, mods: list[str] | None = None) -> list[dict]:
        """Top scores on a beatmap's osu!catch leaderboard, optionally mod-filtered."""
        params = [("mode", "fruits")]
        for m in mods or []:
            params.append(("mods[]", m))
        data = self._get(f"/beatmaps/{beatmap_id}/scores", params=params, allow_404=True)
        return (data or {}).get("scores", [])

    def search_beatmapsets(self, query: str = "", cursor_string: str | None = None) -> dict | None:
        params = {"m": 2, "s": "ranked", "sort": "plays_desc"}
        if query:
            params["q"] = query
        if cursor_string:
            params["cursor_string"] = cursor_string
        return self._get("/beatmapsets/search", params=params, allow_404=False)

    def get_user(self, user, key: str | None = None) -> dict | None:
        params = {"key": key} if key else None
        return self._get(f"/users/{user}", params=params)

    def get_users(self, user_ids: list[int]) -> list[dict]:
        if not user_ids:
            return []
        if len(user_ids) > 50:
            raise ValueError("max 50 users per lookup")
        data = self._get("/users", params=[("ids[]", i) for i in user_ids], allow_404=False)
        return (data or {}).get("users", [])

    def download_map(self, beatmap_id: int, path: str) -> bool:
        self._throttle()
        resp = requests.get(f"{OSU_BASE}/osu/{beatmap_id}", timeout=60)
        if not resp.ok or not resp.content:
            return False
        with open(path, "wb") as fh:
            fh.write(resp.content)
        return True
