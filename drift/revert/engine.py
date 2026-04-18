"""
drift/revert/engine.py

Main orchestration engine for revert operations.
The RevertEngine coordinates all revert subsystems and manages the complete revert workflow.
"""
from __future__ import annotations
import logging
import time
import uuid
from typing import Dict, Optional

from drift.models import Snapshot
from drift.storage import load_snapshot, head_commit
from drift.collectors import run_all

from .models import (
    RevertOptions, RevertResult, OperationPlan, SafetyAssessment,
    RiskLevel, generate_revert_id
)

logger = logging.getLogger("drift.revert.engine")


class RevertEngine:
    """
    Main orchestration engine for revert operations.
    
    Coordinates the entire revert process from diff analysis to execution completion.
    Manages revert session state, error recovery, and rollback operations.
    """
    
    def __init__(self):
        """Initialize the revert engine."""
        self._active_reverts: Dict[str, Dict] = {}
        
    def initiate_revert(self, target_hash: str, options: RevertOptions) -> RevertResult:
        """
        Initiate a complete revert operation.
        
        Args:
            target_hash: Hash of the target snapshot to revert to
            options: Revert configuration options
            
        Returns:
            RevertResult with success status and operation details
        """
        revert_id = generate_revert_id()
        start_time = time.time()
        
        logger.info(f"Initiating revert {revert_id} to {target_hash}")
        
        try:
            # Step 1: Validate inputs and load snapshots
            validation_result = self._validate_revert_request(target_hash, options)
            if not validation_result["valid"]:
                return RevertResult(
                    success=False,
                    revert_id=revert_id,
                    target_hash=target_hash,
                    error_message=validation_result["error"],
                    duration_seconds=time.time() - start_time
                )
            
            current_snapshot = validation_result["current_snapshot"]
            target_snapshot = validation_result["target_snapshot"]
            
            # Step 2: Analyze differences (placeholder - will be implemented in Phase 2)
            logger.info(f"Analyzing differences between current state and {target_hash}")
            diff_result = self._analyze_differences(current_snapshot, target_snapshot)
            
            if not diff_result["changes"]:
                logger.info("No changes detected - revert not needed")
                return RevertResult(
                    success=True,
                    revert_id=revert_id,
                    target_hash=target_hash,
                    operations_executed=0,
                    duration_seconds=time.time() - start_time
                )
            
            # Step 3: Plan operations (placeholder - will be implemented in Phase 3)
            logger.info("Planning revert operations")
            operation_plan = self._plan_operations(diff_result, options)
            
            # Step 4: Safety validation
            logger.info("Performing safety validation")
            safety_assessment = self._validate_safety(operation_plan, options)
            
            if not safety_assessment.safe and not options.force:
                return RevertResult(
                    success=False,
                    revert_id=revert_id,
                    target_hash=target_hash,
                    error_message="Safety validation failed",
                    safety_assessment=safety_assessment,
                    duration_seconds=time.time() - start_time
                )
            
            # Handle user confirmation for risky operations
            if safety_assessment.requires_confirmation:
                logger.info("Requesting user confirmation for high-risk operations")
                from .safety import SafetyValidator
                validator = SafetyValidator()
                
                if not validator.request_user_confirmation(safety_assessment.risks, operation_plan):
                    return RevertResult(
                        success=False,
                        revert_id=revert_id,
                        target_hash=target_hash,
                        error_message="Operation cancelled by user",
                        safety_assessment=safety_assessment,
                        duration_seconds=time.time() - start_time
                    )
            
            # Step 5: Create safety backup (placeholder - will be implemented in Phase 4)
            backup_hash = None
            if options.create_backup and not options.dry_run:
                logger.info("Creating safety backup")
                backup_hash = self._create_safety_backup()
            
            # Step 6: Execute operations or return dry-run result
            if options.dry_run:
                logger.info("Dry-run mode - returning operation plan")
                return RevertResult(
                    success=True,
                    revert_id=revert_id,
                    target_hash=target_hash,
                    operation_plan=operation_plan,
                    safety_assessment=safety_assessment,
                    duration_seconds=time.time() - start_time
                )
            
            # Step 7: Execute the revert (placeholder - will be implemented in Phase 5)
            logger.info(f"Executing revert operations ({operation_plan.total_operations} operations)")
            execution_result = self._execute_operation_plan(operation_plan, revert_id)
            
            # Step 8: Verify final state (placeholder)
            if execution_result["success"]:
                logger.info("Verifying final state")
                verification_result = self._verify_revert_success(target_snapshot)
                if not verification_result["success"]:
                    logger.warning("State verification failed after revert")
            
            return RevertResult(
                success=execution_result["success"],
                revert_id=revert_id,
                target_hash=target_hash,
                backup_hash=backup_hash,
                operations_executed=execution_result["operations_completed"],
                operations_failed=execution_result["operations_failed"],
                duration_seconds=time.time() - start_time,
                error_message=execution_result.get("error_message"),
                failed_operations=execution_result.get("failed_operations", []),
                operation_plan=operation_plan,
                safety_assessment=safety_assessment
            )
            
        except Exception as e:
            logger.error(f"Revert {revert_id} failed with exception: {e}")
            return RevertResult(
                success=False,
                revert_id=revert_id,
                target_hash=target_hash,
                error_message=f"Revert failed: {str(e)}",
                duration_seconds=time.time() - start_time
            )
        finally:
            # Clean up active revert tracking
            self._active_reverts.pop(revert_id, None)
    
    def dry_run_revert(self, target_hash: str, options: RevertOptions = None) -> OperationPlan:
        """
        Perform a dry-run revert to preview operations without executing them.
        
        Args:
            target_hash: Hash of the target snapshot to revert to
            options: Revert configuration options
            
        Returns:
            OperationPlan showing what would be executed
        """
        if options is None:
            options = RevertOptions()
        
        # Force dry-run mode
        options.dry_run = True
        
        result = self.initiate_revert(target_hash, options)
        return result.operation_plan
    
    def get_revert_status(self, revert_id: str) -> Dict:
        """
        Get status of an ongoing revert operation.
        
        Args:
            revert_id: Unique identifier of the revert operation
            
        Returns:
            Dictionary with status information
        """
        if revert_id not in self._active_reverts:
            return {
                "found": False,
                "error": f"Revert operation {revert_id} not found"
            }
        
        return {
            "found": True,
            "status": self._active_reverts[revert_id]
        }
    
    def cancel_revert(self, revert_id: str) -> bool:
        """
        Cancel an ongoing revert operation.
        
        Args:
            revert_id: Unique identifier of the revert operation
            
        Returns:
            True if cancellation was successful, False otherwise
        """
        if revert_id not in self._active_reverts:
            logger.warning(f"Cannot cancel revert {revert_id} - not found")
            return False
        
        # TODO: Implement actual cancellation logic in Phase 5
        logger.info(f"Cancelling revert {revert_id}")
        self._active_reverts[revert_id]["cancelled"] = True
        return True
    
    # Private helper methods (placeholders for future phases)
    
    def _validate_revert_request(self, target_hash: str, options: RevertOptions) -> Dict:
        """
        Validate the revert request and load required snapshots.
        
        Returns:
            Dictionary with validation result and loaded snapshots
        """
        try:
            # Validate target snapshot exists
            target_snapshot = load_snapshot(target_hash)
            if not target_snapshot:
                return {
                    "valid": False,
                    "error": f"Target snapshot {target_hash} not found"
                }
            
            # Get current system state
            logger.info("Taking current system snapshot for comparison")
            current_snapshot = run_all()
            
            return {
                "valid": True,
                "current_snapshot": current_snapshot,
                "target_snapshot": target_snapshot
            }
            
        except Exception as e:
            return {
                "valid": False,
                "error": f"Validation failed: {str(e)}"
            }
    
    def _analyze_differences(self, current_snapshot: Snapshot, target_snapshot: Snapshot) -> Dict:
        """
        Analyze differences between current and target snapshots.
        
        Uses the enhanced DiffAnalyzer for revert-specific analysis.
        """
        from .analyzer import DiffAnalyzer
        
        analyzer = DiffAnalyzer()
        revert_diff = analyzer.analyze_revert_diff(current_snapshot, target_snapshot)
        
        return {
            "changes": revert_diff.changes,
            "revert_diff": revert_diff,
            "categorized_changes": revert_diff.categorized_changes,
            "risk_assessments": revert_diff.risk_assessments,
            "dependencies": revert_diff.dependencies,
            "complexity_score": revert_diff.complexity_score,
            "high_risk_changes": revert_diff.get_high_risk_changes(),
            "infeasible_changes": revert_diff.get_infeasible_changes()
        }
    
    def _plan_operations(self, diff_result: Dict, options: RevertOptions) -> OperationPlan:
        """
        Plan operations based on diff analysis.
        
        Uses the OperationPlanner to convert analyzed changes into executable operations.
        """
        from .planner import OperationPlanner
        
        planner = OperationPlanner()
        revert_diff = diff_result["revert_diff"]
        
        # Convert RevertOptions to planner options
        planner_options = {
            'exclude_categories': options.exclude_categories,
            'force': options.force,
            'timeout': options.timeout_seconds
        }
        
        operation_plan = planner.plan_operations(revert_diff, planner_options)
        
        return operation_plan
    
    def _validate_safety(self, operation_plan: OperationPlan, options: RevertOptions) -> SafetyAssessment:
        """
        Validate safety of the operation plan using the SafetyValidator.
        """
        from .safety import SafetyValidator
        
        validator = SafetyValidator()
        return validator.assess_safety(operation_plan, options)
    
    def _create_safety_backup(self) -> Optional[str]:
        """
        Create a safety backup snapshot before revert using SafetyValidator.
        """
        from .safety import SafetyValidator
        
        validator = SafetyValidator()
        return validator.create_safety_backup()
    
    def _execute_operation_plan(self, operation_plan: OperationPlan, revert_id: str) -> Dict:
        """
        Execute the planned operations.
        
        This is a placeholder - will be implemented in Phase 5 with the ExecutionEngine.
        """
        # For now, return a mock successful result
        # TODO: Implement actual execution in Phase 5
        logger.info("Operation execution not yet implemented")
        
        return {
            "success": True,
            "operations_completed": operation_plan.total_operations,
            "operations_failed": 0
        }
    
    def _verify_revert_success(self, target_snapshot: Snapshot) -> Dict:
        """
        Verify that the revert was successful by comparing final state to target.
        
        This is a placeholder - will be implemented in Phase 5.
        """
        # TODO: Implement actual verification in Phase 5
        logger.info("State verification not yet implemented")
        
        return {
            "success": True,
            "differences": []
        }