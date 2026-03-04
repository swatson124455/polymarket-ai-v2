#!/usr/bin/env python3
"""
Lightweight pre-flight checks: Python version, env vars, deps, optional disk space.
Use before run_ingestion_standalone or in CI. Full system validation: python validate.py
"""
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


def check_python_version() -> bool:
    v = sys.version_info
    if v.major != 3 or v.minor < 9:
        print(f"  [FAIL] Python 3.9+ required; got {v.major}.{v.minor}.{v.micro}")
        return False
    print(f"  [OK] Python {v.major}.{v.minor}.{v.micro}")
    return True


def check_env_vars() -> bool:
    if os.getenv("DATABASE_URL"):
        print("  [OK] DATABASE_URL set")
        return True
    print("  [FAIL] Set DATABASE_URL in .env")
    return False


def check_dependencies() -> bool:
    deps = [
        ("structlog", "structlog"),
        ("httpx", "httpx"),
        ("asyncpg", "asyncpg"),
        ("sqlalchemy", "sqlalchemy"),
    ]
    missing = []
    for mod, pkg in deps:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"  [FAIL] Missing deps: {', '.join(missing)} (pip install -r requirements.txt)")
        return False
    print("  [OK] Core dependencies installed")
    return True


def check_disk_space(min_gb: float = 1.0) -> bool:
    try:
        import shutil
        stat = shutil.disk_usage(_project_root)
        gb = stat.free / (1024 ** 3)
        if gb < min_gb:
            print(f"  [WARN] Low disk space: {gb:.1f} GB free (recommend {min_gb}+ GB)")
            return True  # non-fatal
        print(f"  [OK] Disk space: {gb:.1f} GB free")
        return True
    except Exception as e:
        print(f"  [WARN] Disk check skipped: {e}")
        return True


def check_database_connection() -> bool:
    """Optional: try to init Database and run a simple query."""
    try:
        import asyncio
        from base_engine.data.database import Database

        async def _ping():
            db = Database()
            await db.init()
            if not db.session_factory:
                return False
            async with db.session_factory() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            return True

        if not asyncio.run(_ping()):
            print("  [FAIL] Database init or ping failed (check DATABASE_URL)")
            return False
        print("  [OK] Database connection successful")
        return True
    except Exception as e:
        print(f"  [FAIL] Database check failed: {e}")
        return False


def main() -> int:
    print("Environment validation (pre-flight)")
    print("-" * 50)
    checks = [
        ("Python version", check_python_version),
        ("Env vars", check_env_vars),
        ("Dependencies", check_dependencies),
        ("Disk space", check_disk_space),
        ("Database", check_database_connection),
    ]
    results = []
    for name, fn in checks:
        print(f"\n{name}:")
        results.append(fn())
    print("\n" + "-" * 50)
    if all(results):
        print("All checks passed.")
        return 0
    print("One or more checks failed. Fix and re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
