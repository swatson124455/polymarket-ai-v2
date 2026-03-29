"""
Dataclasses for audit check results and violations.

AuditViolation.details must be non-empty — its contents are hashed to produce
the violation_hash discriminator that ensures two distinct violations of the same
type on the same market are both stored (not deduped away).
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class AuditViolation:
    recon_type: str
    bot_name: str
    market_id: Optional[str]
    severity: str                        # "WARNING" | "CRITICAL"
    details: Dict[str, Any]              # MUST be non-empty — provides hash discriminator
    internal_value: Optional[Decimal] = None
    external_value: Optional[Decimal] = None
    difference: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if not self.details:
            raise ValueError(
                f"AuditViolation.details must be non-empty for recon_type={self.recon_type!r}. "
                "Include identifying fields (e.g. sequence numbers, sizes) so the "
                "violation_hash is unique per distinct finding."
            )


@dataclass
class CheckResult:
    check_name: str
    passed: bool
    violations: List[AuditViolation]
    duration_ms: float
    tables_queried: List[str]
    summary: str
    timed_out: bool = False              # set True by orchestrator on statement_timeout

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "CRITICAL")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "WARNING")
