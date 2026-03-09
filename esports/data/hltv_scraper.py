"""
HLTV / Liquipedia Scraper — team ratings, map win rates, patch notes.

Covers:
  - HLTV.org: CS2 team ratings, map pool stats, match results
  - Liquipedia: LoL/Dota2/Valorant tournament data, rosters

All sync scraping via asyncio.to_thread() to avoid blocking the event loop.
300s cache TTL to respect rate limits and avoid hammering.

Usage::
    scraper = HLTVScraper()
    rating = await scraper.get_team_rating("navi", game="cs2")
    map_rates = await scraper.get_map_win_rates("navi")
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import requests
from structlog import get_logger

logger = get_logger()

_CACHE_TTL = 300.0  # 5 min
_CACHE_MAX = 200


class _BoundedCache:
    """Simple TTL cache with max-size eviction."""

    def __init__(self, max_size: int = _CACHE_MAX, default_ttl: float = _CACHE_TTL):
        self._data: OrderedDict[str, tuple] = OrderedDict()
        self._max_size = max_size
        self._ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)


# CS2 active map pool (updated when Valve changes it)
CS2_MAP_POOL = [
    "ancient", "anubis", "dust2", "inferno",
    "mirage", "nuke", "vertigo",
]

# Default CT/T side win rates per map (CS2 professional average)
CS2_DEFAULT_MAP_SIDES: Dict[str, Dict[str, float]] = {
    "nuke":    {"ct": 0.57, "t": 0.43},
    "ancient": {"ct": 0.55, "t": 0.45},
    "anubis":  {"ct": 0.54, "t": 0.46},
    "vertigo": {"ct": 0.54, "t": 0.46},
    "inferno": {"ct": 0.53, "t": 0.47},
    "mirage":  {"ct": 0.52, "t": 0.48},
    "dust2":   {"ct": 0.48, "t": 0.52},
}


class HLTVScraper:
    """
    Scraper for HLTV.org (CS2) and Liquipedia (multi-game) data.

    All scraping operations run in asyncio.to_thread() to avoid blocking.
    Results are cached with 300s TTL.
    """

    def __init__(self) -> None:
        self._cache = _BoundedCache()

    async def get_team_rating(self, team_name: str, game: str = "cs2") -> Optional[float]:
        """
        Get team rating (0-2.0 scale for HLTV, normalised for others).

        Returns None if team not found or scraping fails.
        """
        cache_key = f"rating:{game}:{team_name.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                rating = await asyncio.to_thread(self._scrape_hltv_team_rating, team_name)
            else:
                # Liquipedia doesn't have ratings — use recent form as proxy
                results = await self.get_recent_results(team_name, game=game, n=20)
                if results:
                    wins = sum(1 for r in results if r.get("won"))
                    rating = wins / len(results) if results else 0.5
                else:
                    rating = None

            if rating is not None:
                self._cache.set(cache_key, rating)
            return rating
        except Exception as exc:
            logger.debug("HLTVScraper: team rating failed", team=team_name, error=str(exc))
            return None

    async def get_map_win_rates(self, team_name: str) -> Dict[str, float]:
        """
        Get CS2 map-specific win rates for a team.

        Returns dict of map_name -> win_rate (0.0-1.0).
        Falls back to neutral 0.50 for maps with insufficient data.
        """
        cache_key = f"maps:{team_name.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            rates = await asyncio.to_thread(self._scrape_hltv_map_stats, team_name)
            if not rates:
                rates = {m: 0.50 for m in CS2_MAP_POOL}
            self._cache.set(cache_key, rates)
            return rates
        except Exception as exc:
            logger.debug("HLTVScraper: map win rates failed", team=team_name, error=str(exc))
            return {m: 0.50 for m in CS2_MAP_POOL}

    async def get_recent_results(
        self, team_name: str, game: str = "cs2", n: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get recent match results for a team.

        Returns list of dicts with: opponent, score, won (bool), date, event.
        """
        cache_key = f"results:{game}:{team_name.lower()}:{n}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                results = await asyncio.to_thread(self._scrape_hltv_results, team_name, n)
            else:
                results = await asyncio.to_thread(self._scrape_liquipedia_results, team_name, game, n)
            results = results or []
            self._cache.set(cache_key, results)
            return results
        except Exception as exc:
            logger.debug("HLTVScraper: recent results failed", team=team_name, error=str(exc))
            return []

    async def get_current_patch_notes(self, game: str) -> Optional[Dict[str, Any]]:
        """
        Get latest patch/update information for a game.

        Returns dict with: version, date, url, major_changes.
        """
        cache_key = f"patch:{game}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                patch = await asyncio.to_thread(self._scrape_cs2_patch)
            else:
                # Other games handled by Riot API / PandaScore
                patch = None

            if patch:
                self._cache.set(cache_key, patch)
            return patch
        except Exception as exc:
            logger.debug("HLTVScraper: patch notes failed", game=game, error=str(exc))
            return None

    async def get_cs2_map_pool(self) -> List[str]:
        """Get current CS2 active duty map pool."""
        return list(CS2_MAP_POOL)

    async def get_map_side_rates(self, map_name: str) -> Dict[str, float]:
        """Get default CT/T win rates for a CS2 map."""
        return CS2_DEFAULT_MAP_SIDES.get(map_name.lower(), {"ct": 0.50, "t": 0.50})

    # ── Sync scraping methods (run in asyncio.to_thread) ──────────────

    _HLTV_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.hltv.org/",
    }
    _HLTV_RATE_LIMIT = 10.0  # seconds between HLTV requests
    _LIQUIPEDIA_RATE_LIMIT = 0.5  # seconds between Liquipedia requests
    _last_hltv_request: float = 0.0
    _last_liquipedia_request: float = 0.0

    def _hltv_get(self, url: str, timeout: float = 10.0) -> Optional[str]:
        """Rate-limited HTTP GET for HLTV. Returns HTML text or None."""
        now = time.monotonic()
        wait = self._HLTV_RATE_LIMIT - (now - HLTVScraper._last_hltv_request)
        if wait > 0:
            time.sleep(wait)
        HLTVScraper._last_hltv_request = time.monotonic()
        try:
            resp = requests.get(url, headers=self._HLTV_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            logger.debug("HLTVScraper: HTTP %d from %s", resp.status_code, url)
        except requests.RequestException as exc:
            logger.debug("HLTVScraper: request failed", url=url, error=str(exc))
        return None

    def _liquipedia_get(self, url: str, timeout: float = 10.0) -> Optional[Dict]:
        """Rate-limited HTTP GET for Liquipedia API. Returns JSON or None."""
        now = time.monotonic()
        wait = self._LIQUIPEDIA_RATE_LIMIT - (now - HLTVScraper._last_liquipedia_request)
        if wait > 0:
            time.sleep(wait)
        HLTVScraper._last_liquipedia_request = time.monotonic()
        headers = {
            "User-Agent": "PolymarketEsportsBot/1.0 (sam@lockes.io)",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("HLTVScraper: Liquipedia HTTP %d", resp.status_code)
        except requests.RequestException as exc:
            logger.debug("HLTVScraper: Liquipedia request failed", error=str(exc))
        return None

    def _scrape_hltv_team_rating(self, team_name: str) -> Optional[float]:
        """Scrape HLTV team rating from ranking page. Returns 0.0-2.0 scale."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.debug("HLTVScraper: beautifulsoup4 not installed")
            return None

        html = self._hltv_get("https://www.hltv.org/ranking/teams")
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")
            name_lower = team_name.lower().strip()

            # HLTV ranking page has ranked-team elements with team name + rating
            for team_div in soup.select(".ranked-team"):
                name_el = team_div.select_one(".name")
                if not name_el:
                    continue
                found_name = name_el.get_text(strip=True).lower()
                if found_name == name_lower or name_lower in found_name:
                    # Extract rating points
                    points_el = team_div.select_one(".points")
                    if points_el:
                        pts_text = points_el.get_text(strip=True)
                        # Format: "842 points" or "(842)"
                        import re
                        m = re.search(r"(\d+)", pts_text)
                        if m:
                            pts = int(m.group(1))
                            # Normalize to 0-2 scale (top team ~1000 pts)
                            return round(min(pts / 500.0, 2.0), 3)
            logger.debug("HLTVScraper: team not found in ranking", team=team_name)
        except Exception as exc:
            logger.debug("HLTVScraper: parse ranking failed", error=str(exc))
        return None

    def _scrape_hltv_map_stats(self, team_name: str) -> Optional[Dict[str, float]]:
        """Scrape HLTV per-map win rates for a team."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None

        # Search for team page to find team ID
        html = self._hltv_get(
            f"https://www.hltv.org/search?query={team_name.replace(' ', '+')}&type=team"
        )
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")
            name_lower = team_name.lower().strip()

            # Find team link in search results
            team_link = None
            for a_tag in soup.select("a[href*='/stats/teams/']"):
                link_text = a_tag.get_text(strip=True).lower()
                if link_text == name_lower or name_lower in link_text:
                    team_link = a_tag.get("href", "")
                    break

            if not team_link:
                return None

            # Fetch team stats page (contains map stats)
            stats_url = f"https://www.hltv.org{team_link}"
            stats_html = self._hltv_get(stats_url)
            if not stats_html:
                return None

            stats_soup = BeautifulSoup(stats_html, "html.parser")
            rates: Dict[str, float] = {}

            # Parse map statistics table
            for map_row in stats_soup.select(".map-stats-container .map-pool-map-holder"):
                map_el = map_row.select_one(".map-pool-map-name")
                wr_el = map_row.select_one(".map-pool-map-wr")
                if not map_el or not wr_el:
                    continue
                map_name = map_el.get_text(strip=True).lower()
                wr_text = wr_el.get_text(strip=True).rstrip("%")
                try:
                    rates[map_name] = round(float(wr_text) / 100.0, 4)
                except ValueError:
                    pass

            return rates if rates else None
        except Exception as exc:
            logger.debug("HLTVScraper: parse map stats failed", error=str(exc))
            return None

    def _scrape_hltv_results(self, team_name: str, n: int) -> List[Dict[str, Any]]:
        """Scrape recent HLTV match results for a CS2 team."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        html = self._hltv_get(
            f"https://www.hltv.org/results?query={team_name.replace(' ', '+')}"
        )
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, "html.parser")
            name_lower = team_name.lower().strip()
            results: List[Dict[str, Any]] = []

            for result_div in soup.select(".result-con"):
                if len(results) >= n:
                    break

                team1_el = result_div.select_one(".team1 .team")
                team2_el = result_div.select_one(".team2 .team")
                score_el = result_div.select_one(".result-score")
                event_el = result_div.select_one(".event-name")

                if not team1_el or not team2_el:
                    continue

                t1 = team1_el.get_text(strip=True)
                t2 = team2_el.get_text(strip=True)

                # Determine if our team is team1 or team2
                is_team1 = t1.lower() == name_lower or name_lower in t1.lower()
                is_team2 = t2.lower() == name_lower or name_lower in t2.lower()
                if not is_team1 and not is_team2:
                    continue

                opponent = t2 if is_team1 else t1
                score = score_el.get_text(strip=True) if score_el else ""
                event = event_el.get_text(strip=True) if event_el else ""

                # Parse score to determine winner
                won = False
                import re
                score_match = re.match(r"(\d+)\s*-\s*(\d+)", score)
                if score_match:
                    s1, s2 = int(score_match.group(1)), int(score_match.group(2))
                    if is_team1:
                        won = s1 > s2
                    else:
                        won = s2 > s1

                results.append({
                    "opponent": opponent,
                    "score": score,
                    "won": won,
                    "event": event,
                })

            return results
        except Exception as exc:
            logger.debug("HLTVScraper: parse results failed", error=str(exc))
            return []

    def _scrape_liquipedia_results(
        self, team_name: str, game: str, n: int
    ) -> List[Dict[str, Any]]:
        """Fetch recent results from Liquipedia API for non-CS2 games."""
        _GAME_WIKIS = {
            "lol": "leagueoflegends",
            "dota2": "dota2",
            "valorant": "valorant",
            "cod": "callofduty",
            "r6": "rainbowsix",
            "sc2": "starcraft2",
            "rl": "rocketleague",
        }

        wiki = _GAME_WIKIS.get(game)
        if not wiki:
            return []

        # Liquipedia API: fetch team page and parse match results
        url = (
            f"https://liquipedia.net/{wiki}/api.php"
            f"?action=parse&page={team_name.replace(' ', '_')}"
            f"&prop=wikitext&format=json"
        )

        data = self._liquipedia_get(url)
        if not data or "parse" not in data:
            return []

        try:
            wikitext = data["parse"].get("wikitext", {}).get("*", "")
            if not wikitext:
                return []

            # Parse recent match entries from wikitext
            # Liquipedia match history format: {{Match|...}}
            import re
            results: List[Dict[str, Any]] = []
            match_blocks = re.findall(
                r"\{\{MatchMaps\|([^}]+)\}\}|\{\{Match\|([^}]+)\}\}",
                wikitext,
            )

            for block in match_blocks[:n]:
                text = block[0] or block[1]
                opponent_m = re.search(r"opponent\d?=([^|]+)", text)
                score_m = re.search(r"score=(\d+)-(\d+)", text)
                won_m = re.search(r"win=(\d)", text)

                opponent = opponent_m.group(1).strip() if opponent_m else "Unknown"
                score = f"{score_m.group(1)}-{score_m.group(2)}" if score_m else ""
                won = won_m.group(1) == "1" if won_m else False

                results.append({
                    "opponent": opponent,
                    "score": score,
                    "won": won,
                    "event": "",
                })

            return results
        except Exception as exc:
            logger.debug("HLTVScraper: Liquipedia parse failed", error=str(exc))
            return []

    def _scrape_cs2_patch(self) -> Optional[Dict[str, Any]]:
        """Scrape latest CS2 patch info from counter-strike.net."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None

        html = self._hltv_get("https://www.counter-strike.net/news/updates")
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Find most recent update post
            update_el = soup.select_one(
                ".inner_post, .post_content, article"
            )
            if not update_el:
                return None

            title_el = update_el.select_one("h1, h2, .post_title")
            date_el = update_el.select_one("time, .post_date, .date")

            title = title_el.get_text(strip=True) if title_el else "CS2 Update"
            date_str = ""
            if date_el:
                date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)

            # Extract major changes (list items)
            changes = []
            for li in update_el.select("li")[:10]:
                text = li.get_text(strip=True)
                if text and len(text) > 5:
                    changes.append(text)

            return {
                "version": title,
                "date": date_str,
                "url": "https://www.counter-strike.net/news/updates",
                "major_changes": changes[:5],
            }
        except Exception as exc:
            logger.debug("HLTVScraper: CS2 patch parse failed", error=str(exc))
            return None
