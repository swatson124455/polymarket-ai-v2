#!/usr/bin/env python3
"""
Polymarket AI V2 — System Validation Script.

Validates the full system before first run or after deployment:
  1. Python version (3.9+)
  2. Environment variables (.env)
  3. Core dependencies
  4. Database connectivity + migrations
  5. Redis connectivity
  6. File permissions (data/ directory)
  7. ML model dependencies
  8. API endpoint reachability

Usage:
  python validate.py                    # Full validation
  python validate.py --no-migrate       # Skip migration check
  python validate.py --skip-startup-checks  # Skip DB/Redis connectivity
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


class Validator:
    """Runs all validation checks and reports results."""

    def __init__(self, no_migrate: bool = False, skip_startup: bool = False):
        self.no_migrate = no_migrate
        self.skip_startup = skip_startup
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def ok(self, msg: str):
        print(f"  [\033[92mOK\033[0m]   {msg}")
        self.passed += 1

    def fail(self, msg: str):
        print(f"  [\033[91mFAIL\033[0m] {msg}")
        self.failed += 1

    def warn(self, msg: str):
        print(f"  [\033[93mWARN\033[0m] {msg}")
        self.warnings += 1

    # ── 1. Python version ────────────────────────────────────────────────

    def check_python(self):
        print("\n[1/8] Python version")
        v = sys.version_info
        if v.major != 3 or v.minor < 9:
            self.fail(f"Python 3.9+ required, got {v.major}.{v.minor}.{v.micro}")
        else:
            self.ok(f"Python {v.major}.{v.minor}.{v.micro}")

    # ── 2. Environment variables ─────────────────────────────────────────

    def check_env(self):
        print("\n[2/8] Environment variables")

        db_url = os.getenv("DATABASE_URL", "")
        if db_url and "password" not in db_url:
            self.ok(f"DATABASE_URL set ({db_url[:40]}...)")
        elif db_url:
            self.warn("DATABASE_URL set but may contain default password")
        else:
            self.fail("DATABASE_URL not set")

        if os.getenv("REDIS_ENABLED", "true").lower() == "true":
            if os.getenv("REDIS_PASSWORD"):
                self.ok("REDIS_PASSWORD set")
            else:
                self.warn("REDIS_PASSWORD not set (Redis will use no auth)")
        else:
            self.ok("Redis disabled (REDIS_ENABLED=false)")

        # API keys (optional but important)
        api_checks = [
            ("CLOB_API_KEY", "Polymarket CLOB API"),
            ("ANTHROPIC_API_KEY", "Anthropic (LLM signals)"),
        ]
        for key, label in api_checks:
            if os.getenv(key):
                self.ok(f"{label} configured")
            else:
                self.warn(f"{label} not set ({key}) — feature disabled")

    # ── 3. Core dependencies ─────────────────────────────────────────────

    def check_dependencies(self):
        print("\n[3/8] Core dependencies")

        required = [
            ("structlog", "structlog"),
            ("httpx", "httpx"),
            ("asyncpg", "asyncpg"),
            ("sqlalchemy", "sqlalchemy"),
            ("sklearn", "scikit-learn"),
            ("xgboost", "xgboost"),
            ("numpy", "numpy"),
            ("pydantic", "pydantic"),
            ("dotenv", "python-dotenv"),
        ]
        optional = [
            ("lightgbm", "lightgbm"),
            ("catboost", "catboost"),
            ("feedparser", "feedparser"),
            ("redis", "redis"),
            ("web3", "web3"),
            ("torch", "torch"),
        ]

        for mod, pkg in required:
            try:
                __import__(mod)
                self.ok(f"{pkg}")
            except ImportError:
                self.fail(f"{pkg} not installed (pip install {pkg})")

        for mod, pkg in optional:
            try:
                __import__(mod)
                self.ok(f"{pkg} (optional)")
            except ImportError:
                self.warn(f"{pkg} not installed (optional — feature disabled)")

    # ── 4. Database connectivity ─────────────────────────────────────────

    async def check_database(self):
        print("\n[4/8] Database connectivity")
        if self.skip_startup:
            self.warn("Skipped (--skip-startup-checks)")
            return

        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            self.fail("No DATABASE_URL — cannot check DB")
            return

        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            url = db_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

            engine = create_async_engine(url, echo=False)
            async with engine.connect() as conn:
                from sqlalchemy import text
                result = await conn.execute(text("SELECT 1"))
                result.fetchone()
            await engine.dispose()
            self.ok("PostgreSQL connection successful")
        except Exception as e:
            self.fail(f"PostgreSQL connection failed: {e}")

    # ── 5. Migrations ────────────────────────────────────────────────────

    async def check_migrations(self):
        print("\n[5/8] Database migrations")
        if self.no_migrate:
            self.warn("Skipped (--no-migrate)")
            return
        if self.skip_startup:
            self.warn("Skipped (--skip-startup-checks)")
            return

        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            self.fail("No DATABASE_URL — cannot check migrations")
            return

        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy import text
            url = db_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

            engine = create_async_engine(url, echo=False)
            async with engine.connect() as conn:
                # Check if markets table exists (created by migration 001)
                result = await conn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'markets')"
                ))
                exists = result.scalar()
                if exists:
                    self.ok("Core tables exist (markets, trades, etc.)")
                else:
                    self.warn("Tables not yet created — run: python scripts/run_migrations.py")

                # Count tables
                result = await conn.execute(text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
                ))
                count = result.scalar()
                self.ok(f"{count} tables in database")

            await engine.dispose()
        except Exception as e:
            self.fail(f"Migration check failed: {e}")

    # ── 6. Redis connectivity ────────────────────────────────────────────

    async def check_redis(self):
        print("\n[6/8] Redis connectivity")
        if self.skip_startup:
            self.warn("Skipped (--skip-startup-checks)")
            return

        if os.getenv("REDIS_ENABLED", "true").lower() != "true":
            self.ok("Redis disabled — skipping")
            return

        try:
            import redis.asyncio as aioredis
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", "6379"))
            password = os.getenv("REDIS_PASSWORD")
            r = aioredis.Redis(host=host, port=port, password=password, decode_responses=True)
            await r.ping()
            await r.aclose()
            self.ok(f"Redis connected ({host}:{port})")
        except ImportError:
            self.warn("redis package not installed (pip install redis)")
        except Exception as e:
            self.warn(f"Redis connection failed: {e} (system will run without cache)")

    # ── 7. File permissions ──────────────────────────────────────────────

    def check_files(self):
        print("\n[7/8] File system")

        data_dir = _project_root / "data"
        if data_dir.exists():
            self.ok(f"data/ directory exists")
            # Check writable
            test_file = data_dir / ".write_test"
            try:
                test_file.write_text("test")
                test_file.unlink()
                self.ok("data/ directory is writable")
            except PermissionError:
                self.fail("data/ directory is not writable")
        else:
            try:
                data_dir.mkdir(parents=True)
                self.ok("data/ directory created")
            except Exception as e:
                self.fail(f"Cannot create data/ directory: {e}")

        # Check key config files
        if (_project_root / ".env").exists():
            self.ok(".env file exists")
        else:
            self.fail(".env file missing (cp .env.example .env)")

        if (_project_root / "config" / "settings.py").exists():
            self.ok("config/settings.py exists")
        else:
            self.fail("config/settings.py missing")

        # Check disk space
        try:
            import shutil
            total, used, free = shutil.disk_usage(str(_project_root))
            free_gb = free / (1024 ** 3)
            if free_gb < 1.0:
                self.fail(f"Low disk space: {free_gb:.1f} GB free (need 1+ GB)")
            else:
                self.ok(f"Disk space: {free_gb:.1f} GB free")
        except Exception:
            self.warn("Could not check disk space")

    # ── 8. API reachability ──────────────────────────────────────────────

    async def check_apis(self):
        print("\n[8/8] API reachability")
        if self.skip_startup:
            self.warn("Skipped (--skip-startup-checks)")
            return

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Polymarket CLOB
                try:
                    r = await client.get("https://clob.polymarket.com/time")
                    if r.status_code == 200:
                        self.ok("Polymarket CLOB API reachable")
                    else:
                        self.warn(f"Polymarket CLOB returned {r.status_code}")
                except Exception as e:
                    self.warn(f"Polymarket CLOB unreachable: {e}")

                # Gamma Markets API
                try:
                    r = await client.get("https://gamma-api.polymarket.com/markets?limit=1")
                    if r.status_code == 200:
                        self.ok("Polymarket Gamma API reachable")
                    else:
                        self.warn(f"Gamma API returned {r.status_code}")
                except Exception as e:
                    self.warn(f"Gamma API unreachable: {e}")

        except ImportError:
            self.warn("httpx not installed — cannot check APIs")

    # ── Run all checks ───────────────────────────────────────────────────

    async def run_all(self):
        print("=" * 50)
        print("  Polymarket AI V2 — System Validation")
        print("=" * 50)

        self.check_python()
        self.check_env()
        self.check_dependencies()
        await self.check_database()
        await self.check_migrations()
        await self.check_redis()
        self.check_files()
        await self.check_apis()

        print("\n" + "=" * 50)
        print(f"  Results: {self.passed} passed, {self.failed} failed, {self.warnings} warnings")
        print("=" * 50)

        if self.failed > 0:
            print("\n  Some checks failed. Fix issues above before proceeding.")
            return 1
        elif self.warnings > 0:
            print("\n  All critical checks passed. Warnings are non-blocking.")
            return 0
        else:
            print("\n  All checks passed. System is ready.")
            return 0


def main():
    parser = argparse.ArgumentParser(description="Polymarket AI V2 system validation")
    parser.add_argument("--no-migrate", action="store_true", help="Skip migration checks")
    parser.add_argument("--skip-startup-checks", action="store_true", help="Skip DB/Redis/API connectivity checks")
    args = parser.parse_args()

    validator = Validator(
        no_migrate=args.no_migrate,
        skip_startup=args.skip_startup_checks,
    )
    exit_code = asyncio.run(validator.run_all())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
