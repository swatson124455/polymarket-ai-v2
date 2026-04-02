"""
Patch Drift Detector — monitors game patches and model degradation.

Detection signals:
  1. Rolling Brier score (20-game window) — >5% degradation triggers warning
  2. Riot API patch version check — new patch → 48h observation mode
  3. Champion win rate shift >3% — triggers retrain flag
  4. Model calibration break — predicted 70% but actual 55% over 30 games → halt

Observation mode: 48h after new patch, bot runs paper-only (no live trades).
This mirrors Riot's own approach of continuous retraining with SageMaker.

Usage::
    detector = PatchDriftDetector(riot_client)
    await detector.check_all_games()
    if detector.is_observation_mode("lol"):
        # Paper-trade only for 48h after new patch
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from structlog import get_logger

logger = get_logger()

_BRIER_WINDOW = 20         # Rolling window for Brier score
_BRIER_DEGRADATION = 0.25  # Brier above no-skill baseline triggers warning
_WINRATE_SHIFT = 0.03      # 3% champion win rate shift triggers retrain
_OBSERVATION_HOURS = 48    # Paper-only period after new patch (minor default)
_CALIBRATION_WINDOW = 30   # Window for calibration check
_CALIBRATION_THRESHOLD = 0.15  # 15% gap between predicted and actual → halt

# S136 Phase 7D: Patch severity keyword sets
_MAJOR_KEYWORDS = {"rework", "overhaul", "economy", "new map", "major update", "new agent", "new champion"}
_MINOR_KEYWORDS = {"balance", "patch", "update", "nerf", "buff", "adjustment"}
_HOTFIX_KEYWORDS = {"hotfix", "bugfix", "crash fix", "server fix", "stability"}

# Observation hours by severity tier
_SEVERITY_OBSERVATION_HOURS = {
    "hotfix": 0,    # Skip observation entirely
    "minor": 48,    # Current default, unchanged
    "major": 168,   # 7 days — RD inflation needed
}


def _classify_patch_severity(patch_notes: str) -> str:
    """Classify a patch into hotfix / minor / major based on keyword matching.

    Returns one of: ``"hotfix"``, ``"minor"``, ``"major"``.
    """
    notes_lower = patch_notes.lower()
    # Hotfix check first (most specific)
    if any(kw in notes_lower for kw in _HOTFIX_KEYWORDS):
        return "hotfix"
    # Major check (multiple keywords = higher confidence)
    _major_hits = sum(1 for kw in _MAJOR_KEYWORDS if kw in notes_lower)
    if _major_hits >= 2:
        return "major"
    # Default to minor
    return "minor"


class PatchDriftDetector:
    """
    Monitors game patches and model performance for drift.

    Tracks patch versions per game, computes rolling Brier scores,
    and flags when models need retraining or observation mode.
    """

    def __init__(self, riot_client=None, hltv_scraper=None, observation_hours: int = 48) -> None:
        self._riot_client = riot_client
        self._hltv_scraper = hltv_scraper
        self._observation_hours = observation_hours

        # Per-game state
        self._known_patches: Dict[str, str] = {}        # game → last known patch version
        self._patch_timestamps: Dict[str, _dt.datetime] = {}  # game → when patch was detected
        self._patch_severity: Dict[str, str] = {}  # S136 7D: game → severity ("hotfix"/"minor"/"major")
        self._predictions: Dict[str, List[Tuple[float, float]]] = {}  # game → [(predicted, actual)]
        self._champion_baselines: Dict[str, Dict[str, float]] = {}  # game → {champion: win_rate}
        self._halted_games: set = set()  # games where trading is halted

    async def check_all_games(self) -> Dict[str, Dict[str, Any]]:
        """
        Run all drift checks for all supported games.

        Returns dict of game → status dict with keys:
          observation_mode, should_retrain, brier_ok, calibration_ok, halted
        """
        results = {}
        for game in ("lol", "cs2", "dota2", "valorant"):
            results[game] = await self.check_game(game)
        return results

    async def check_game(self, game: str) -> Dict[str, Any]:
        """Run all drift checks for a single game."""
        status = {
            "observation_mode": False,
            "should_retrain": False,
            "brier_ok": True,
            "calibration_ok": True,
            "halted": game in self._halted_games,
        }

        # Check patch version
        new_patch = await self._check_patch_version(game)
        if new_patch:
            status["observation_mode"] = True
            status["should_retrain"] = True
            logger.info(
                "PatchDriftDetector: new patch detected",
                game=game,
                patch=new_patch,
                observation_hours=_OBSERVATION_HOURS,
            )

        # Check observation mode (may be from previous check)
        if self.is_observation_mode(game):
            status["observation_mode"] = True

        # Brier score check
        brier = self.compute_brier_score(game)
        if brier is not None and brier > _BRIER_DEGRADATION:
            status["brier_ok"] = False
            status["should_retrain"] = True
            logger.warning(
                "PatchDriftDetector: Brier score degraded",
                game=game,
                brier_score=round(brier, 4),
                threshold=_BRIER_DEGRADATION,
            )

        # Calibration check
        cal_gap = self._check_calibration(game)
        if cal_gap is not None and cal_gap > _CALIBRATION_THRESHOLD:
            status["calibration_ok"] = False
            self._halted_games.add(game)
            status["halted"] = True
            logger.error(
                "PatchDriftDetector: calibration broken — halting trading",
                game=game,
                calibration_gap=round(cal_gap, 4),
            )

        return status

    def is_observation_mode(self, game: str) -> bool:
        """
        Check if a game is in observation mode (paper-only after new patch).

        S136 Phase 7D: Uses severity-based observation hours.
        - hotfix: 0h (skip observation entirely)
        - minor: 48h (default)
        - major: 168h (7 days)

        Returns True for observation_hours after a new patch is detected.
        """
        ts = self._patch_timestamps.get(game)
        if ts is None:
            return False

        # S136 7D: Use severity-specific observation hours
        severity = self._patch_severity.get(game, "minor")
        obs_hours = _SEVERITY_OBSERVATION_HOURS.get(severity, self._observation_hours)
        if obs_hours <= 0:
            return False  # hotfix — skip observation

        now = _dt.datetime.now(_dt.timezone.utc)
        hours_since = (now - ts).total_seconds() / 3600.0
        return hours_since < obs_hours

    def should_retrain(self, game: str) -> bool:
        """Check if model should be retrained for this game."""
        brier = self.compute_brier_score(game)
        if brier is not None and brier > _BRIER_DEGRADATION:
            return True

        # Check if we recently detected a new patch
        ts = self._patch_timestamps.get(game)
        if ts is not None:
            now = _dt.datetime.now(_dt.timezone.utc)
            hours_since = (now - ts).total_seconds() / 3600.0
            if hours_since < _OBSERVATION_HOURS:
                return True

        return False

    def get_patch_severity(self, game: str) -> Optional[str]:
        """S154: Return the severity of the most recent patch for a game.

        Returns ``"hotfix"``, ``"minor"``, ``"major"``, or None if no patch detected.
        """
        return self._patch_severity.get(game)

    def is_halted(self, game: str) -> bool:
        """Check if trading is halted for this game due to calibration failure."""
        return game in self._halted_games

    def unhalt(self, game: str) -> None:
        """Manually unhalt a game after retraining."""
        self._halted_games.discard(game)

    # ── Brier Score ─────────────────────────────────────────────────────

    def record_prediction(self, game: str, predicted: float, actual: float) -> None:
        """
        Record a prediction outcome for Brier score tracking.

        Args:
            game: Game title.
            predicted: Model's predicted probability (0-1).
            actual: Actual outcome (0 or 1).
        """
        preds = self._predictions.setdefault(game, [])
        preds.append((predicted, actual))
        # Keep only last 100 predictions
        if len(preds) > 100:
            self._predictions[game] = preds[-100:]

    def compute_brier_score(self, game: str, window: int = _BRIER_WINDOW) -> Optional[float]:
        """
        Compute rolling Brier score over the last N predictions.

        Brier score = (1/N) * sum((predicted - actual)^2)
        Perfect = 0.0, worst = 1.0, no-skill baseline = 0.25.

        Returns None if insufficient data.
        """
        preds = self._predictions.get(game, [])
        if len(preds) < window:
            return None

        recent = preds[-window:]
        brier = sum((p - a) ** 2 for p, a in recent) / len(recent)
        return brier

    # ── Champion Win Rate Shift ─────────────────────────────────────────

    def set_champion_baseline(self, game: str, baselines: Dict[str, float]) -> None:
        """Set baseline champion win rates from training data."""
        self._champion_baselines[game] = dict(baselines)

    def check_champion_drift(
        self, game: str, current_rates: Dict[str, float]
    ) -> List[str]:
        """
        Check which champions have shifted >3% from training baseline.

        Returns list of drifted champion names.
        """
        baselines = self._champion_baselines.get(game, {})
        if not baselines:
            return []

        drifted = []
        for champ, current_rate in current_rates.items():
            baseline_rate = baselines.get(champ)
            if baseline_rate is not None and abs(current_rate - baseline_rate) > _WINRATE_SHIFT:
                drifted.append(champ)

        if drifted:
            logger.info(
                "PatchDriftDetector: champion drift detected",
                game=game,
                drifted_count=len(drifted),
                examples=drifted[:5],
            )

        return drifted

    # ── Internal helpers ────────────────────────────────────────────────

    async def _check_patch_version(self, game: str) -> Optional[str]:
        """Check if a new patch has been released for a game.

        S136 Phase 7D: Classifies patch severity and sets observation hours accordingly.
        """
        if game == "lol" and self._riot_client:
            try:
                version = await self._riot_client.get_current_patch_version()
                if version and version != self._known_patches.get(game):
                    old = self._known_patches.get(game)
                    self._known_patches[game] = version
                    if old is not None:  # Don't trigger on first check
                        severity = _classify_patch_severity(version)
                        self._patch_severity[game] = severity
                        self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)
                        _obs_h = _SEVERITY_OBSERVATION_HOURS.get(severity, _OBSERVATION_HOURS)
                        logger.info("PatchDriftDetector: patch classified",
                                    game=game, severity=severity, observation_hours=_obs_h)
                        if severity == "major":
                            logger.warning("PatchDriftDetector: MAJOR patch — 7-day observation, RD inflation needed",
                                           game=game, version=version)
                        return version
            except Exception as exc:
                logger.debug("PatchDriftDetector: LoL patch check failed", error=str(exc))

        elif game == "cs2" and self._hltv_scraper:
            try:
                patch = await self._hltv_scraper.get_current_patch_notes("cs2")
                if patch:
                    version = str(patch.get("version", ""))
                    _notes_text = str(patch.get("notes", patch.get("title", version)))
                    if version and version != self._known_patches.get(game):
                        old = self._known_patches.get(game)
                        self._known_patches[game] = version
                        if old is not None:
                            severity = _classify_patch_severity(_notes_text)
                            self._patch_severity[game] = severity
                            self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)
                            _obs_h = _SEVERITY_OBSERVATION_HOURS.get(severity, _OBSERVATION_HOURS)
                            logger.info("PatchDriftDetector: patch classified",
                                        game=game, severity=severity, observation_hours=_obs_h)
                            if severity == "major":
                                logger.warning("PatchDriftDetector: MAJOR patch — 7-day observation, RD inflation needed",
                                               game=game, version=version)
                            return version
            except Exception as exc:
                logger.debug("PatchDriftDetector: CS2 patch check failed", error=str(exc))

        elif game == "dota2":
            try:
                version = await self._fetch_dota2_patch_version()
                if version and version != self._known_patches.get(game):
                    old = self._known_patches.get(game)
                    self._known_patches[game] = version
                    if old is not None:
                        severity = _classify_patch_severity(version)
                        self._patch_severity[game] = severity
                        self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)
                        _obs_h = _SEVERITY_OBSERVATION_HOURS.get(severity, _OBSERVATION_HOURS)
                        logger.info("PatchDriftDetector: patch classified",
                                    game=game, severity=severity, observation_hours=_obs_h)
                        if severity == "major":
                            logger.warning("PatchDriftDetector: MAJOR patch — 7-day observation, RD inflation needed",
                                           game=game, version=version)
                        return version
            except Exception as exc:
                logger.debug("PatchDriftDetector: Dota2 patch check failed", error=str(exc))

        elif game == "valorant":
            try:
                version = await self._fetch_valorant_patch_version()
                if version and version != self._known_patches.get(game):
                    old = self._known_patches.get(game)
                    self._known_patches[game] = version
                    if old is not None:
                        severity = _classify_patch_severity(version)
                        self._patch_severity[game] = severity
                        self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)
                        _obs_h = _SEVERITY_OBSERVATION_HOURS.get(severity, _OBSERVATION_HOURS)
                        logger.info("PatchDriftDetector: patch classified",
                                    game=game, severity=severity, observation_hours=_obs_h)
                        if severity == "major":
                            logger.warning("PatchDriftDetector: MAJOR patch — 7-day observation, RD inflation needed",
                                           game=game, version=version)
                        return version
            except Exception as exc:
                logger.debug("PatchDriftDetector: Valorant patch check failed", error=str(exc))

        return None

    async def _fetch_dota2_patch_version(self) -> Optional[str]:
        """Fetch current Dota2 patch version from Steam News API."""
        import httpx
        url = (
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
            "?appid=570&count=5&feeds=steam_community_announcements"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("appnews", {}).get("newsitems", [])
        for item in items:
            title = str(item.get("title", "")).lower()
            # Dota2 patch notes: require "gameplay update", "patch", or version number
            if ("gameplay update" in title or "patch" in title or
                    re.search(r'\d+\.\d+', title)):
                # Exclude known non-gameplay patterns
                if any(x in title for x in (
                    "client", "workshop", "community", "cosmetic",
                    "server", "maintenance",
                )):
                    continue
                # Use the gid (unique news ID) as version identifier
                gid = str(item.get("gid", ""))
                if gid:
                    return gid
        return None

    async def _fetch_valorant_patch_version(self) -> Optional[str]:
        """Fetch current Valorant patch version from valorant-api.com."""
        import httpx
        url = "https://valorant-api.com/v1/version"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        version_data = data.get("data", {})
        version = str(version_data.get("riotClientVersion", ""))
        return version if version else None

    def _check_calibration(self, game: str) -> Optional[float]:
        """
        Check model calibration: gap between predicted probability and actual outcomes.

        Returns the absolute gap, or None if insufficient data.
        """
        preds = self._predictions.get(game, [])
        if len(preds) < _CALIBRATION_WINDOW:
            return None

        recent = preds[-_CALIBRATION_WINDOW:]
        avg_predicted = sum(p for p, _ in recent) / len(recent)
        avg_actual = sum(a for _, a in recent) / len(recent)
        gap = abs(avg_predicted - avg_actual)
        return gap
