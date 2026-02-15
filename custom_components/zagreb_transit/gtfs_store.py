"""GTFS feed storage and validity selection for Zagreb Transit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import csv
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import zipfile

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    GTFS_LISTING_URL,
    GTFS_PORTAL_URL,
    MAX_CACHED_FEEDS,
    MAX_LISTING_CANDIDATES_TO_TRY,
    MAX_PREVIOUS_VERSION_TRIES,
    STATIC_GTFS_URL,
)

_LOGGER = logging.getLogger(__name__)


def _safe_version(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _meta_rank(meta: "FeedMeta") -> tuple[int, date]:
    """Rank feed recency. Higher is newer."""
    version_rank = int(meta.version) if meta.version.isdigit() else -1
    start_rank = meta.start_date or date.min
    return (version_rank, start_rank)


@dataclass(slots=True)
class FeedMeta:
    """Metadata for one cached static feed."""

    version: str
    start_date: date | None
    end_date: date | None
    file_path: str
    source: str
    downloaded_at: str

    @property
    def valid_range(self) -> str:
        start = self.start_date.isoformat() if self.start_date else "unknown"
        end = self.end_date.isoformat() if self.end_date else "unknown"
        return f"{start} -> {end}"

    def is_valid_for(self, day: date) -> bool:
        if self.start_date and day < self.start_date:
            return False
        if self.end_date and day > self.end_date:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "file_path": self.file_path,
            "source": self.source,
            "downloaded_at": self.downloaded_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "FeedMeta":
        start = date.fromisoformat(raw["start_date"]) if raw.get("start_date") else None
        end = date.fromisoformat(raw["end_date"]) if raw.get("end_date") else None
        return cls(
            version=raw.get("version", "unknown"),
            start_date=start,
            end_date=end,
            file_path=raw.get("file_path", ""),
            source=raw.get("source", "local"),
            downloaded_at=raw.get("downloaded_at", ""),
        )


class GtfsStore:
    """Manage static GTFS download, caching and active feed selection."""

    def __init__(self, hass: HomeAssistant, session: ClientSession) -> None:
        self.hass = hass
        self.session = session
        self.base_dir = Path(hass.config.path(".storage", DOMAIN))
        self.feeds_dir = self.base_dir / "feeds"
        self.state_path = self.base_dir / "state.json"
        self._dirs_ready = False
        self.debug: dict = {
            "today": None,
            "latest_version": None,
            "latest_valid_range": None,
            "listing_candidates": [],
            "tried_listing_urls": [],
            "tried_version_urls": [],
            "selected_strategy": "none",
            "listing_attempts": 0,
            "version_attempts": 0,
        }

    async def refresh_latest(self) -> FeedMeta:
        """Download latest GTFS feed and cache locally."""
        await self._ensure_dirs()
        _LOGGER.debug("Downloading static GTFS feed from %s", STATIC_GTFS_URL)
        async with self.session.get(STATIC_GTFS_URL, timeout=60) as response:
            response.raise_for_status()
            payload = await response.read()

        digest = hashlib.sha256(payload).hexdigest()[:12]
        feed_info = await self.hass.async_add_executor_job(self._extract_feed_info, payload)
        version_raw = feed_info.get("feed_version") or f"hash_{digest}"
        version = _safe_version(version_raw)

        zip_path = self.feeds_dir / f"{version}.zip"
        meta_path = self.feeds_dir / f"{version}.json"
        cached = await self._load_cached_meta_if_present(zip_path, meta_path)
        if cached:
            return cached
        await self.hass.async_add_executor_job(zip_path.write_bytes, payload)

        meta = FeedMeta(
            version=version,
            start_date=_parse_date(feed_info.get("feed_start_date")),
            end_date=_parse_date(feed_info.get("feed_end_date")),
            file_path=str(zip_path),
            source="latest",
            downloaded_at=datetime.utcnow().isoformat(),
        )

        meta_json = json.dumps(meta.to_dict(), indent=2)
        await self.hass.async_add_executor_job(meta_path.write_text, meta_json, "utf-8")
        await self.prune_old_feeds(keep_versions=MAX_CACHED_FEEDS)
        return meta

    async def refresh_from_url(self, feed_url: str, source: str) -> FeedMeta:
        """Download GTFS feed from arbitrary URL and cache it."""
        await self._ensure_dirs()
        _LOGGER.debug("Downloading GTFS feed from %s", feed_url)
        async with self.session.get(feed_url, timeout=60) as response:
            response.raise_for_status()
            payload = await response.read()

        digest = hashlib.sha256(payload).hexdigest()[:12]
        feed_info = await self.hass.async_add_executor_job(self._extract_feed_info, payload)
        version_raw = feed_info.get("feed_version") or f"hash_{digest}"
        version = _safe_version(version_raw)

        zip_path = self.feeds_dir / f"{version}.zip"
        meta_path = self.feeds_dir / f"{version}.json"
        cached = await self._load_cached_meta_if_present(zip_path, meta_path)
        if cached:
            return cached
        await self.hass.async_add_executor_job(zip_path.write_bytes, payload)

        meta = FeedMeta(
            version=version,
            start_date=_parse_date(feed_info.get("feed_start_date")),
            end_date=_parse_date(feed_info.get("feed_end_date")),
            file_path=str(zip_path),
            source=source,
            downloaded_at=datetime.utcnow().isoformat(),
        )

        meta_json = json.dumps(meta.to_dict(), indent=2)
        await self.hass.async_add_executor_job(meta_path.write_text, meta_json, "utf-8")
        await self.prune_old_feeds(keep_versions=MAX_CACHED_FEEDS)
        return meta

    async def refresh_previous_from_listing(self, today: date) -> FeedMeta | None:
        """Fetch GTFS listing pages and download best valid previous candidate."""
        await self._ensure_dirs()
        all_candidates: list[str] = []
        seen: set[str] = set()
        pages = [GTFS_LISTING_URL, GTFS_PORTAL_URL]

        for page_url in pages:
            try:
                async with self.session.get(page_url, timeout=30) as response:
                    response.raise_for_status()
                    html = await response.text()
                candidates = self._extract_listing_candidates(html, page_url)
                for candidate in candidates:
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    all_candidates.append(candidate)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to load GTFS listing page %s: %s", page_url, err)

        self.debug["listing_candidates"] = all_candidates
        if len(all_candidates) < 2:
            _LOGGER.warning("GTFS listing fallback found less than 2 candidates: %s", all_candidates)
            return None

        valid_metas: list[FeedMeta] = []
        attempts = 0

        # Skip first candidate (equivalent to latest), then inspect archived ones.
        for fallback_url in all_candidates[1:]:
            if attempts >= MAX_LISTING_CANDIDATES_TO_TRY:
                break
            attempts += 1
            self.debug["tried_listing_urls"].append(fallback_url)
            _LOGGER.info("Trying listing fallback feed: %s", fallback_url)
            try:
                meta = await self.refresh_from_url(fallback_url, source="listing_previous")
                if meta.is_valid_for(today):
                    valid_metas.append(meta)
                    continue
                _LOGGER.warning(
                    "Listing fallback candidate not valid for today. version=%s range=%s",
                    meta.version,
                    meta.valid_range,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed downloading listing fallback feed %s: %s", fallback_url, err)

        if not valid_metas:
            self.debug["listing_attempts"] = attempts
            return None
        best = max(valid_metas, key=_meta_rank)
        self.debug["selected_strategy"] = "listing_previous"
        self.debug["listing_attempts"] = attempts
        return best

    async def refresh_previous_from_version(self, latest_version: str, today: date) -> FeedMeta | None:
        """Try previous numeric versions based on latest feed version."""
        await self._ensure_dirs()
        if not latest_version.isdigit():
            return None

        width = len(latest_version)
        current = int(latest_version)
        base = STATIC_GTFS_URL.rsplit("/", 1)[0]

        attempts = 0
        for offset in range(1, MAX_PREVIOUS_VERSION_TRIES + 1):
            candidate = str(current - offset).zfill(width)
            if int(candidate) <= 0:
                break
            attempts += 1
            candidate_url = f"{base}/{candidate}"
            self.debug["tried_version_urls"].append(candidate_url)
            try:
                meta = await self.refresh_from_url(candidate_url, source="version_previous")
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Previous version candidate failed %s: %s", candidate_url, err)
                continue

            if meta.is_valid_for(today):
                _LOGGER.info(
                    "Using previous version candidate %s valid for %s",
                    meta.version,
                    today.isoformat(),
                )
                self.debug["selected_strategy"] = "version_previous"
                self.debug["version_attempts"] = attempts
                return meta

            _LOGGER.warning(
                "Previous version candidate not valid for today. version=%s range=%s",
                meta.version,
                meta.valid_range,
            )

        self.debug["version_attempts"] = attempts
        return None

    async def list_cached_feeds(self) -> list[FeedMeta]:
        """Return cached feeds sorted newest first."""
        await self._ensure_dirs()
        feeds: list[FeedMeta] = []
        meta_files = await self.hass.async_add_executor_job(
            lambda: sorted(self.feeds_dir.glob("*.json"))
        )
        for meta_file in meta_files:
            try:
                text = await self.hass.async_add_executor_job(meta_file.read_text, "utf-8")
                raw = json.loads(text)
                feeds.append(FeedMeta.from_dict(raw))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed loading feed meta %s: %s", meta_file, err)

        feeds.sort(key=_meta_rank, reverse=True)
        return feeds

    async def get_active_feed(self, today: date, latest_meta: FeedMeta | None) -> tuple[FeedMeta | None, str, str]:
        """Select active feed for today."""
        self.debug.update(
            {
                "today": today.isoformat(),
                "latest_version": latest_meta.version if latest_meta else None,
                "latest_valid_range": latest_meta.valid_range if latest_meta else None,
                "listing_candidates": [],
                "tried_listing_urls": [],
                "tried_version_urls": [],
                "selected_strategy": "none",
                "listing_attempts": 0,
                "version_attempts": 0,
            }
        )
        if latest_meta and latest_meta.is_valid_for(today):
            self.debug["selected_strategy"] = "latest"
            await self._save_state({"active_version": latest_meta.version, "status": "ok"})
            return latest_meta, "latest", "ok"

        if latest_meta:
            _LOGGER.warning(
                "Latest GTFS feed is not valid for today (%s). latest=%s range=%s",
                today.isoformat(),
                latest_meta.version,
                latest_meta.valid_range,
            )
        else:
            _LOGGER.warning("Latest GTFS feed unavailable, trying listing fallback")

        # Strategy 1: derive previous feed from latest numeric version.
        if latest_meta:
            previous_by_version = await self.refresh_previous_from_version(latest_meta.version, today)
            if previous_by_version:
                await self._save_state({"active_version": previous_by_version.version, "status": "version_previous"})
                return previous_by_version, "version_previous", "ok"

        # Strategy 2: scrape listing page and take previous candidate.
        previous_meta = await self.refresh_previous_from_listing(today)
        if previous_meta:
            await self._save_state({"active_version": previous_meta.version, "status": "listing_previous"})
            return previous_meta, "listing_previous", "ok"

        cached = await self.list_cached_feeds()
        valid = [item for item in cached if item.is_valid_for(today)]
        if valid:
            selected = valid[0]
            self.debug["selected_strategy"] = "fallback_local"
            await self._save_state({"active_version": selected.version, "status": "fallback_local"})
            return selected, "fallback_local", "ok"

        state = await self._load_state()
        active_version = state.get("active_version")
        if active_version:
            fallback = next((item for item in cached if item.version == active_version), None)
            if fallback:
                self.debug["selected_strategy"] = "fallback_local_degraded"
                return fallback, "fallback_local", "degraded"

        self.debug["selected_strategy"] = "none"
        return None, "none", "degraded"

    async def load_feed_bytes(self, meta: FeedMeta) -> bytes:
        """Load cached zip bytes for selected feed."""
        await self._ensure_dirs()
        path = Path(meta.file_path)
        return await self.hass.async_add_executor_job(path.read_bytes)

    async def force_select(self, version: str) -> FeedMeta | None:
        """Force active feed version if locally available."""
        cached = await self.list_cached_feeds()
        selected = next((item for item in cached if item.version == version), None)
        if selected:
            await self._save_state({"active_version": selected.version, "status": "forced"})
        return selected

    async def prune_old_feeds(self, keep_versions: int = MAX_CACHED_FEEDS) -> None:
        """Delete old cached GTFS feed files and metadata."""
        cached = await self.list_cached_feeds()
        for meta in cached[max(1, keep_versions):]:
            zip_path = Path(meta.file_path)
            meta_path = zip_path.with_suffix(".json")
            try:
                if await self.hass.async_add_executor_job(zip_path.exists):
                    await self.hass.async_add_executor_job(zip_path.unlink)
                if await self.hass.async_add_executor_job(meta_path.exists):
                    await self.hass.async_add_executor_job(meta_path.unlink)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed pruning cached feed %s: %s", zip_path, err)

    def _extract_feed_info(self, payload: bytes) -> dict[str, str]:
        """Extract one row from feed_info.txt."""
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.namelist()
            feed_info_member = next(
                (name for name in members if name.lower().endswith("/feed_info.txt") or name.lower() == "feed_info.txt"),
                None,
            )
            if not feed_info_member:
                return {}
            with archive.open(feed_info_member) as handle:
                text = handle.read().decode("utf-8-sig", errors="replace")

        rows = list(csv.DictReader(io.StringIO(text)))
        return rows[0] if rows else {}

    async def _load_state(self) -> dict:
        await self._ensure_dirs()
        exists = await self.hass.async_add_executor_job(self.state_path.exists)
        if not exists:
            return {}
        try:
            text = await self.hass.async_add_executor_job(self.state_path.read_text, "utf-8")
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return {}

    async def _save_state(self, data: dict) -> None:
        await self._ensure_dirs()
        state_json = json.dumps(data, indent=2)
        await self.hass.async_add_executor_job(self.state_path.write_text, state_json, "utf-8")

    async def _load_cached_meta_if_present(self, zip_path: Path, meta_path: Path) -> FeedMeta | None:
        if not await self.hass.async_add_executor_job(zip_path.exists):
            return None
        if not await self.hass.async_add_executor_job(meta_path.exists):
            return None
        try:
            text = await self.hass.async_add_executor_job(meta_path.read_text, "utf-8")
            raw = json.loads(text)
            meta = FeedMeta.from_dict(raw)
            if not meta.file_path:
                meta.file_path = str(zip_path)
            return meta
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed loading cached feed meta %s: %s", meta_path, err)
            return None

    async def _ensure_dirs(self) -> None:
        if self._dirs_ready:
            return
        await self.hass.async_add_executor_job(self._mkdir_dirs)
        self._dirs_ready = True

    def _mkdir_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.feeds_dir.mkdir(parents=True, exist_ok=True)

    def _extract_listing_candidates(self, html: str, base_url: str) -> list[str]:
        """Extract GTFS feed hrefs from listing page, preserving order and deduplicating."""
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        full_urls: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            url = urljoin(base_url, href)
            if not self._is_gtfs_candidate(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            full_urls.append(url)
        return full_urls

    def _is_gtfs_candidate(self, url: str) -> bool:
        """Return True if URL looks like downloadable GTFS candidate."""
        parsed = urlparse(url)
        path = parsed.path.lower()
        if "/gtfs-scheduled/latest" in path:
            return True
        if path.endswith(".zip"):
            return True
        # ZET often exposes feeds as /gtfs-scheduled/<version> without .zip suffix
        if "/gtfs-scheduled/" in path:
            tail = path.rstrip("/").split("/")[-1]
            if tail and tail != "latest":
                return True
        return False
