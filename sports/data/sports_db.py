"""
ORM tables for the sports betting infrastructure — Migration 022.

IMPORTANT: Imports Base and NaiveUTCDateTime from base_engine.data.database.
NEVER redeclare Base here — all models must share the same declarative metadata
so that create_all() and migration tooling see every table in one place.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from base_engine.data.database import Base, NaiveUTCDateTime, _naive_utc


def _now() -> datetime:
    """Return naive UTC now (helper to keep column defaults terse)."""
    return _naive_utc(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# sports_players
# ---------------------------------------------------------------------------

class SportsPlayer(Base):
    """One row per unique (external_id, sport) player identity.

    name_variants: JSONB list of aliases used for fuzzy matching.
    e.g. ["Pat Mahomes", "PM15", "Patrick Mahomes II"]
    """
    __tablename__ = "sports_players"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    external_id = Column(String(100), nullable=True)
    name = Column(String(200), nullable=False)
    name_variants = Column(JSON, default=list, nullable=False)   # JSONB in PG
    team_id = Column(String(100), nullable=True)
    position = Column(String(20), nullable=True)
    sport = Column(String(20), nullable=False)
    status = Column(String(50), default="active", nullable=False)
    created_at = Column(NaiveUTCDateTime, default=_now)
    updated_at = Column(NaiveUTCDateTime, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("external_id", "sport", name="uq_sports_players_external_sport"),
        Index("idx_sports_players_sport_status", "sport", "status"),
        # GIN index for JSONB containment queries on name_variants
        Index(
            "idx_sports_players_name_variants_gin",
            "name_variants",
            postgresql_using="gin",
        ),
    )

    def __repr__(self) -> str:
        return f"<SportsPlayer id={self.id} name={self.name!r} sport={self.sport}>"


# ---------------------------------------------------------------------------
# sports_teams
# ---------------------------------------------------------------------------

class SportsTeam(Base):
    """Static reference table for teams (seeded from SportsDataIO)."""
    __tablename__ = "sports_teams"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    external_id = Column(String(100), nullable=True)
    name = Column(String(200), nullable=False)
    abbreviation = Column(String(10), nullable=True)
    sport = Column(String(20), nullable=False)
    conference = Column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("external_id", "sport", name="uq_sports_teams_external_sport"),
    )

    def __repr__(self) -> str:
        return f"<SportsTeam id={self.id} name={self.name!r} sport={self.sport}>"


# ---------------------------------------------------------------------------
# sports_games
# ---------------------------------------------------------------------------

class SportsGame(Base):
    """One row per scheduled / live / completed game.

    weather_summary: JSONB dict — {wind_mph, temp_f, precip_pct}
    Critical for NFL outdoor games and MLB/Soccer.
    """
    __tablename__ = "sports_games"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    external_id = Column(String(100), nullable=True)
    home_team_id = Column(String(100), nullable=True)
    away_team_id = Column(String(100), nullable=True)
    sport = Column(String(20), nullable=False)
    start_time = Column(NaiveUTCDateTime, nullable=False)
    # scheduled / live / final / postponed
    status = Column(String(30), default="scheduled", nullable=False)
    score_home = Column(Integer, nullable=True)
    score_away = Column(Integer, nullable=True)
    venue = Column(String(200), nullable=True)
    # {wind_mph: float, temp_f: float, precip_pct: float}
    weather_summary = Column(JSON, nullable=True)
    created_at = Column(NaiveUTCDateTime, default=_now)
    updated_at = Column(NaiveUTCDateTime, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("external_id", "sport", name="uq_sports_games_external_sport"),
        Index("idx_sports_games_start_status", "start_time", "status"),
        Index("idx_sports_games_sport_status", "sport", "status"),
    )

    def __repr__(self) -> str:
        return f"<SportsGame id={self.id} sport={self.sport} status={self.status}>"


# ---------------------------------------------------------------------------
# sports_injury_events
# ---------------------------------------------------------------------------

class SportsInjuryEvent(Base):
    """One row per detected injury / status-change signal.

    Sources: twitter / rss / reddit / discord / telegram / manual
    NLP tiers: regex / spacy / llm
    detected_status: out / doubtful / questionable / day-to-day / free-agent-move
    severity: season_ending / multi_week / day-to-day / offseason_move
    """
    __tablename__ = "sports_injury_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    player_id = Column(BigInteger, nullable=True)
    game_id = Column(BigInteger, nullable=True)
    source = Column(String(50), nullable=False)
    source_url = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=False)
    player_raw = Column(String(200), nullable=True)       # raw name from text
    detected_status = Column(String(50), nullable=True)
    severity = Column(String(30), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    nlp_tier = Column(String(20), nullable=True)          # regex / spacy / llm
    detected_at = Column(NaiveUTCDateTime, nullable=False)
    bet_triggered = Column(Boolean, default=False, nullable=False)
    bet_market_id = Column(String(200), nullable=True)

    __table_args__ = (
        Index("idx_sports_injury_player_game", "player_id", "game_id"),
        Index("idx_sports_injury_detected_at", "detected_at"),
        Index("idx_sports_injury_source_player", "source", "player_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<SportsInjuryEvent id={self.id} player_raw={self.player_raw!r} "
            f"status={self.detected_status} conf={self.confidence:.2f}>"
        )


# ---------------------------------------------------------------------------
# sports_market_map
# ---------------------------------------------------------------------------

class SportsMarketMap(Base):
    """Maps a game to one or more prediction market listings.

    Stores both Polymarket token IDs (yes_token_id / no_token_id) and
    Kalshi ticker (market_id). current_price refreshed every 120s by scanner.
    """
    __tablename__ = "sports_market_map"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    game_id = Column(BigInteger, nullable=True)
    platform = Column(String(30), nullable=False)         # polymarket / kalshi
    market_id = Column(String(200), nullable=False)
    market_type = Column(String(50), nullable=True)
    sport = Column(String(20), nullable=True)
    yes_token_id = Column(String(200), nullable=True)     # Polymarket YES token
    no_token_id = Column(String(200), nullable=True)      # Polymarket NO token
    current_price = Column(Float, nullable=True)
    mapped_at = Column(NaiveUTCDateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("platform", "market_id", name="uq_sports_market_map_platform_market"),
        Index("idx_sports_market_map_game", "game_id"),
        Index("idx_sports_market_map_sport_platform", "sport", "platform"),
    )

    def __repr__(self) -> str:
        return f"<SportsMarketMap id={self.id} platform={self.platform} market_id={self.market_id!r}>"


# ---------------------------------------------------------------------------
# sports_calibration
# ---------------------------------------------------------------------------

class SportsCalibration(Base):
    """Per-(sport, market_type) calibration for adaptive Kelly.

    AdaptiveKelly reads brier_score here to adjust kelly_fraction:
      brier > 0.30 → 0.10× | brier < 0.20 → 0.50× | default 0.25×
    Updated by HealthScheduler every SPORTS_CALIBRATION_UPDATE_INTERVAL seconds.
    """
    __tablename__ = "sports_calibration"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sport = Column(String(20), nullable=False)
    market_type = Column(String(50), nullable=False)
    bet_count = Column(Integer, default=0, nullable=False)
    correct_count = Column(Integer, default=0, nullable=False)
    brier_score = Column(Float, nullable=True)
    kelly_fraction = Column(Float, default=0.25, nullable=False)
    last_updated = Column(NaiveUTCDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("sport", "market_type", name="uq_sports_calibration_sport_market"),
    )

    def __repr__(self) -> str:
        return (
            f"<SportsCalibration sport={self.sport} market_type={self.market_type} "
            f"brier={self.brier_score} kelly={self.kelly_fraction}>"
        )


# ---------------------------------------------------------------------------
# sports_live_events
# ---------------------------------------------------------------------------

class SportsLiveEvent(Base):
    """In-game events detected by SportsLiveBot event_detector.

    event_type: blowout / player_going_off / momentum_shift / tennis_retirement_risk
    elapsed_pct: 0.0–1.0 fraction of game elapsed
    score_diff: absolute scoring advantage (home - away)
    """
    __tablename__ = "sports_live_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    game_id = Column(BigInteger, nullable=True)
    sport = Column(String(20), nullable=True)
    event_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    elapsed_pct = Column(Float, nullable=True)
    score_diff = Column(Integer, nullable=True)
    detected_at = Column(NaiveUTCDateTime, nullable=False)
    bet_triggered = Column(Boolean, default=False, nullable=False)
    bet_market_id = Column(String(200), nullable=True)

    __table_args__ = (
        Index("idx_sports_live_events_game_detected", "game_id", "detected_at"),
        Index("idx_sports_live_events_type_detected", "event_type", "detected_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<SportsLiveEvent id={self.id} game_id={self.game_id} "
            f"type={self.event_type} elapsed={self.elapsed_pct}>"
        )
