"""
drift/revert/__init__.py

Auto-revert functionality for drift - transforms drift from a passive state tracker
into an active system management tool that can automatically revert to known-good states.

Main entry points:
- revert_to_snapshot(): Execute a complete revert operation
- dry_run_revert(): Preview what a revert would do without executing
- get_revert_status(): Check status of ongoing revert operations
"""
from __future__ import annotations

from .engine import RevertEngine
from .models import (
    RevertOptions, Operation, RevertResult, OperationPlan, 
    SafetyAssessment, RiskLevel, Risk
)
from .analyzer import DiffAnalyzer, RevertDiff
from .enhanced_change import EnhancedChange, ChangeEnhancer, enhance_diff_changes
from .planner import OperationPlanner, CommandGenerator
from .dependency import DependencyGraph, DependencyBuilder, validate_operation_plan_dependencies
from .safety import SafetyValidator, SystemValidator, RiskAssessor, SafetyBackupManager, UserConfirmationManager

__all__ = [
    "RevertEngine",
    "RevertOptions", 
    "Operation",
    "RevertResult", 
    "OperationPlan",
    "SafetyAssessment",
    "RiskLevel",
    "Risk",
    "DiffAnalyzer",
    "RevertDiff",
    "EnhancedChange",
    "ChangeEnhancer",
    "enhance_diff_changes",
    "OperationPlanner",
    "CommandGenerator", 
    "DependencyGraph",
    "DependencyBuilder",
    "validate_operation_plan_dependencies",
    "SafetyValidator",
    "SystemValidator",
    "RiskAssessor", 
    "SafetyBackupManager",
    "UserConfirmationManager",
    "revert_to_snapshot",
    "dry_run_revert",
    "get_revert_status",
]

# Convenience functions for common operations
def revert_to_snapshot(target_hash: str, options: RevertOptions = None) -> RevertResult:
    """
    Revert system state to match the specified snapshot.
    
    Args:
        target_hash: Hash of the target snapshot to revert to
        options: Revert options (dry_run, force, exclude_categories, etc.)
        
    Returns:
        RevertResult with success status and operation details
    """
    if options is None:
        options = RevertOptions()
    
    engine = RevertEngine()
    return engine.initiate_revert(target_hash, options)


def dry_run_revert(target_hash: str, options: RevertOptions = None) -> OperationPlan:
    """
    Preview what operations would be executed for a revert without actually executing them.
    
    Args:
        target_hash: Hash of the target snapshot to revert to
        options: Revert options (exclude_categories, etc.)
        
    Returns:
        OperationPlan showing what would be executed
    """
    if options is None:
        options = RevertOptions()
    
    # Force dry-run mode
    options.dry_run = True
    
    engine = RevertEngine()
    result = engine.initiate_revert(target_hash, options)
    
    # Return the operation plan from the dry run result
    return result.operation_plan


def get_revert_status(revert_id: str) -> dict:
    """
    Get status of an ongoing revert operation.
    
    Args:
        revert_id: Unique identifier of the revert operation
        
    Returns:
        Dictionary with status information
    """
    engine = RevertEngine()
    return engine.get_revert_status(revert_id)