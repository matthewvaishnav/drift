"""drift — git-like server state tracker"""
__version__ = "0.1.0"

# Import revert functionality
from drift.revert import (
    revert_to_snapshot,
    dry_run_revert,
    get_revert_status,
    RevertEngine,
    RevertOptions,
    RevertResult,
    OperationPlan,
    SafetyAssessment,
    RiskLevel
)

__all__ = [
    "revert_to_snapshot",
    "dry_run_revert", 
    "get_revert_status",
    "RevertEngine",
    "RevertOptions",
    "RevertResult",
    "OperationPlan",
    "SafetyAssessment",
    "RiskLevel"
]
