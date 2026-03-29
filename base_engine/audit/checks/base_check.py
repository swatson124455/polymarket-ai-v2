"""
Abstract base class for all audit checks.

Contract:
- Checks must NOT manage their own DB sessions or timeouts.
  The orchestrator opens the session, sets READ COMMITTED isolation,
  sets statement_timeout='30s', and passes the session in.
- checks must NOT modify any trading tables.
- details dict in every AuditViolation must be non-empty.
"""
from abc import ABC, abstractmethod
from typing import List

from base_engine.audit.check_result import CheckResult


class BaseCheck(ABC):
    # Subclasses must define these
    name: str = ""
    tables_queried: List[str] = []

    @abstractmethod
    async def execute(self, session) -> CheckResult:
        """
        Run all checks for this class against the provided SQLAlchemy AsyncSession.
        The session already has READ COMMITTED isolation and a 30s statement_timeout.
        Return a CheckResult regardless of outcome — do not raise.
        """
        ...
