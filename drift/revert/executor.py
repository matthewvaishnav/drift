"""
drift/revert/executor.py

Execution engine for revert operations.
Provides secure command execution, progress monitoring, rollback capabilities,
and comprehensive error handling for revert operations.
"""
from __future__ import annotations
import asyncio
import logging
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any, Tuple
from pathlib import Path

from .models import (
    Operation, OperationPlan, OperationBatch, ExecutionResult, 
    RevertOptions, RiskLevel
)

logger = logging.getLogger("drift.revert.executor")


class ExecutionStatus(Enum):
    """Status of operation execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


@dataclass
class ExecutionContext:
    """
    Context for operation execution.
    
    Tracks execution state, progress, and provides cancellation support.
    """
    operation_id: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    process: Optional[subprocess.Popen] = None
    stdout: str = ""
    stderr: str = ""
    return_code: Optional[int] = None
    cancelled: bool = False
    rollback_executed: bool = False
    error_message: Optional[str] = None
    
    @property
    def duration(self) -> float:
        """Get execution duration in seconds."""
        if self.start_time is None:
            return 0.0
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def is_running(self) -> bool:
        """Check if execution is currently running."""
        return self.status == ExecutionStatus.RUNNING
    
    @property
    def is_complete(self) -> bool:
        """Check if execution is complete (success or failure)."""
        return self.status in [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, 
                              ExecutionStatus.CANCELLED, ExecutionStatus.ROLLED_BACK]


@dataclass
class ProgressInfo:
    """
    Progress information for execution monitoring.
    """
    total_operations: int
    completed_operations: int = 0
    failed_operations: int = 0
    current_operation: Optional[str] = None
    current_batch: Optional[str] = None
    estimated_remaining_seconds: float = 0.0
    overall_progress_percent: float = 0.0
    
    def update_progress(self):
        """Update calculated progress fields."""
        if self.total_operations > 0:
            self.overall_progress_percent = (
                (self.completed_operations + self.failed_operations) / 
                self.total_operations * 100
            )


class CommandExecutor:
    """
    Secure command executor with privilege handling and timeout support.
    
    Handles the actual execution of system commands with proper security,
    timeout management, and output capture.
    """
    
    def __init__(self):
        """Initialize the command executor."""
        self.default_timeout = 300  # 5 minutes
        self.max_output_size = 1024 * 1024  # 1MB
    
    def execute_command(
        self, 
        command: str, 
        timeout: Optional[int] = None,
        working_dir: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None
    ) -> Tuple[int, str, str]:
        """
        Execute a system command securely.
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (uses default if None)
            working_dir: Working directory for command execution
            env_vars: Additional environment variables
            
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if timeout is None:
            timeout = self.default_timeout
        
        # Prepare environment
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        
        # Security: Validate command to prevent injection
        if not self._validate_command_security(command):
            raise ValueError(f"Command failed security validation: {command}")
        
        logger.info(f"Executing command: {command}")
        
        try:
            # Execute command with timeout
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=working_dir,
                env=env,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return_code = process.returncode
                
                # Truncate output if too large
                stdout = self._truncate_output(stdout)
                stderr = self._truncate_output(stderr)
                
                logger.info(f"Command completed with return code: {return_code}")
                return return_code, stdout, stderr
                
            except subprocess.TimeoutExpired:
                logger.warning(f"Command timed out after {timeout}s: {command}")
                
                # Kill the process group
                if hasattr(os, 'killpg'):
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        time.sleep(2)  # Give it time to terminate gracefully
                        if process.poll() is None:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Process already terminated
                else:
                    process.terminate()
                    time.sleep(2)
                    if process.poll() is None:
                        process.kill()
                
                stdout, stderr = process.communicate()
                stdout = self._truncate_output(stdout or "")
                stderr = self._truncate_output(stderr or "")
                
                return -1, stdout, f"Command timed out after {timeout}s\n{stderr}"
        
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return -1, "", str(e)
    
    def _validate_command_security(self, command: str) -> bool:
        """
        Validate command for basic security.
        
        Args:
            command: Command to validate
            
        Returns:
            True if command passes basic security checks
        """
        # Basic security checks
        dangerous_patterns = [
            ';rm -rf', '&& rm -rf', '| rm -rf',
            ';dd if=', '&& dd if=', '| dd if=',
            'format c:', 'del /f /s /q',
            '$(', '`', '${',  # Command substitution
            '>/dev/null && rm', '2>/dev/null && rm'
        ]
        
        command_lower = command.lower()
        for pattern in dangerous_patterns:
            if pattern in command_lower:
                logger.warning(f"Command contains dangerous pattern: {pattern}")
                return False
        
        # Check for excessively long commands (potential buffer overflow)
        if len(command) > 2048:
            logger.warning("Command is excessively long")
            return False
        
        return True
    
    def _truncate_output(self, output: str) -> str:
        """Truncate output if it exceeds maximum size."""
        if len(output) <= self.max_output_size:
            return output
        
        truncated = output[:self.max_output_size]
        truncated += f"\n... [Output truncated - {len(output)} total bytes]"
        return truncated


class RollbackManager:
    """
    Manages rollback operations for failed executions.
    
    Tracks executed operations and provides rollback capabilities
    when operations fail or need to be undone.
    """
    
    def __init__(self, executor: CommandExecutor):
        """Initialize the rollback manager."""
        self.executor = executor
        self.executed_operations: List[Operation] = []
        self.rollback_stack: List[Operation] = []
    
    def record_successful_operation(self, operation: Operation):
        """
        Record a successfully executed operation for potential rollback.
        
        Args:
            operation: Operation that was successfully executed
        """
        self.executed_operations.append(operation)
        
        # Add to rollback stack if rollback command is available
        if operation.rollback_command:
            self.rollback_stack.append(operation)
            logger.debug(f"Added operation {operation.id} to rollback stack")
    
    def execute_rollback(self, failed_operation: Operation) -> ExecutionResult:
        """
        Execute rollback for all operations up to the failed operation.
        
        Args:
            failed_operation: The operation that failed
            
        Returns:
            ExecutionResult with rollback status
        """
        logger.info(f"Starting rollback due to failed operation: {failed_operation.id}")
        
        rollback_results = []
        operations_rolled_back = 0
        rollback_failures = []
        
        # Execute rollbacks in reverse order (LIFO)
        while self.rollback_stack:
            operation = self.rollback_stack.pop()
            
            logger.info(f"Rolling back operation: {operation.id}")
            
            try:
                return_code, stdout, stderr = self.executor.execute_command(
                    operation.rollback_command,
                    timeout=operation.estimated_duration * 2  # Give more time for rollback
                )
                
                if return_code == 0:
                    operations_rolled_back += 1
                    logger.info(f"Successfully rolled back operation: {operation.id}")
                else:
                    rollback_failures.append(operation)
                    logger.error(f"Rollback failed for operation {operation.id}: {stderr}")
                
                rollback_results.append({
                    'operation_id': operation.id,
                    'success': return_code == 0,
                    'return_code': return_code,
                    'stdout': stdout,
                    'stderr': stderr
                })
                
            except Exception as e:
                rollback_failures.append(operation)
                logger.error(f"Rollback exception for operation {operation.id}: {e}")
                
                rollback_results.append({
                    'operation_id': operation.id,
                    'success': False,
                    'error': str(e)
                })
        
        # Create rollback execution result
        rollback_success = len(rollback_failures) == 0
        
        return ExecutionResult(
            success=rollback_success,
            operations_completed=operations_rolled_back,
            operations_failed=len(rollback_failures),
            duration_seconds=0.0,  # TODO: Track rollback duration
            failed_operations=rollback_failures,
            rollback_performed=True
        )


class ProgressMonitor:
    """
    Monitors and reports progress of operation execution.
    
    Provides real-time progress updates, estimated completion times,
    and progress callbacks for UI integration.
    """
    
    def __init__(self):
        """Initialize the progress monitor."""
        self.progress_info = ProgressInfo(total_operations=0)
        self.callbacks: List[Callable[[ProgressInfo], None]] = []
        self.start_time: Optional[float] = None
        self.operation_times: List[float] = []
    
    def add_progress_callback(self, callback: Callable[[ProgressInfo], None]):
        """
        Add a progress callback function.
        
        Args:
            callback: Function to call with progress updates
        """
        self.callbacks.append(callback)
    
    def start_monitoring(self, total_operations: int):
        """
        Start progress monitoring.
        
        Args:
            total_operations: Total number of operations to execute
        """
        self.progress_info = ProgressInfo(total_operations=total_operations)
        self.start_time = time.time()
        self.operation_times = []
        self._notify_callbacks()
    
    def update_current_operation(self, operation_id: str, batch_id: Optional[str] = None):
        """
        Update the currently executing operation.
        
        Args:
            operation_id: ID of the current operation
            batch_id: ID of the current batch (optional)
        """
        self.progress_info.current_operation = operation_id
        self.progress_info.current_batch = batch_id
        self._notify_callbacks()
    
    def record_operation_completion(self, operation_id: str, success: bool, duration: float):
        """
        Record completion of an operation.
        
        Args:
            operation_id: ID of the completed operation
            success: Whether the operation succeeded
            duration: Time taken for the operation
        """
        if success:
            self.progress_info.completed_operations += 1
        else:
            self.progress_info.failed_operations += 1
        
        self.operation_times.append(duration)
        self._update_time_estimates()
        self.progress_info.update_progress()
        self._notify_callbacks()
    
    def _update_time_estimates(self):
        """Update estimated remaining time based on completed operations."""
        if not self.operation_times or not self.start_time:
            return
        
        # Calculate average operation time
        avg_operation_time = sum(self.operation_times) / len(self.operation_times)
        
        # Estimate remaining operations
        remaining_operations = (
            self.progress_info.total_operations - 
            self.progress_info.completed_operations - 
            self.progress_info.failed_operations
        )
        
        # Estimate remaining time
        self.progress_info.estimated_remaining_seconds = remaining_operations * avg_operation_time
    
    def _notify_callbacks(self):
        """Notify all registered progress callbacks."""
        for callback in self.callbacks:
            try:
                callback(self.progress_info)
            except Exception as e:
                logger.error(f"Progress callback failed: {e}")


class ExecutionEngine:
    """
    Main execution engine for revert operations.
    
    Coordinates secure command execution, progress monitoring, error handling,
    and rollback operations for complete revert workflow execution.
    """
    
    def __init__(self):
        """Initialize the execution engine."""
        self.command_executor = CommandExecutor()
        self.rollback_manager = RollbackManager(self.command_executor)
        self.progress_monitor = ProgressMonitor()
        self.execution_contexts: Dict[str, ExecutionContext] = {}
        self.cancelled = False
        self._executor_pool = ThreadPoolExecutor(max_workers=4)
    
    def execute_operation_plan(
        self, 
        plan: OperationPlan, 
        options: RevertOptions,
        progress_callback: Optional[Callable[[ProgressInfo], None]] = None
    ) -> ExecutionResult:
        """
        Execute a complete operation plan.
        
        Args:
            plan: Operation plan to execute
            options: Revert options
            progress_callback: Optional progress callback function
            
        Returns:
            ExecutionResult with execution status and details
        """
        logger.info(f"Starting execution of operation plan with {plan.total_operations} operations")
        
        # Setup progress monitoring
        if progress_callback:
            self.progress_monitor.add_progress_callback(progress_callback)
        
        self.progress_monitor.start_monitoring(plan.total_operations)
        
        start_time = time.time()
        operations_completed = 0
        operations_failed = 0
        failed_operations = []
        
        try:
            # Execute batches sequentially
            for batch_idx, batch in enumerate(plan.batches):
                if self.cancelled:
                    logger.info("Execution cancelled by user")
                    break
                
                logger.info(f"Executing batch {batch_idx + 1}/{len(plan.batches)}: {batch.id}")
                
                # Execute operations in batch (potentially in parallel)
                batch_result = self._execute_batch(batch, options)
                
                operations_completed += batch_result.operations_completed
                operations_failed += batch_result.operations_failed
                failed_operations.extend(batch_result.failed_operations)
                
                # If batch failed and we're not forcing, stop execution
                if batch_result.operations_failed > 0 and not options.force:
                    logger.error(f"Batch {batch.id} failed, stopping execution")
                    
                    # Execute rollback if enabled
                    if not options.dry_run:
                        logger.info("Executing rollback for failed operations")
                        rollback_result = self.rollback_manager.execute_rollback(
                            failed_operations[-1] if failed_operations else None
                        )
                        
                        return ExecutionResult(
                            success=False,
                            operations_completed=operations_completed,
                            operations_failed=operations_failed,
                            duration_seconds=time.time() - start_time,
                            failed_operations=failed_operations,
                            rollback_performed=rollback_result.rollback_performed
                        )
                    break
        
        except Exception as e:
            logger.error(f"Execution failed with exception: {e}")
            return ExecutionResult(
                success=False,
                operations_completed=operations_completed,
                operations_failed=operations_failed + 1,
                duration_seconds=time.time() - start_time,
                error_message=str(e),
                failed_operations=failed_operations
            )
        
        # Determine overall success
        success = operations_failed == 0 and not self.cancelled
        
        logger.info(f"Execution complete: {operations_completed} completed, {operations_failed} failed")
        
        return ExecutionResult(
            success=success,
            operations_completed=operations_completed,
            operations_failed=operations_failed,
            duration_seconds=time.time() - start_time,
            failed_operations=failed_operations
        )
    
    def _execute_batch(self, batch: OperationBatch, options: RevertOptions) -> ExecutionResult:
        """
        Execute a single batch of operations.
        
        Args:
            batch: Batch to execute
            options: Revert options
            
        Returns:
            ExecutionResult for the batch
        """
        logger.info(f"Executing batch {batch.id} with {len(batch.operations)} operations")
        
        batch_start_time = time.time()
        completed = 0
        failed = 0
        failed_ops = []
        
        # For now, execute operations sequentially within batch
        # TODO: Implement parallel execution for independent operations
        for operation in batch.operations:
            if self.cancelled:
                break
            
            self.progress_monitor.update_current_operation(operation.id, batch.id)
            
            # Execute single operation
            op_result = self._execute_single_operation(operation, options)
            
            if op_result.success:
                completed += 1
                self.rollback_manager.record_successful_operation(operation)
            else:
                failed += 1
                failed_ops.extend(op_result.failed_operations)
            
            # Record progress
            self.progress_monitor.record_operation_completion(
                operation.id, 
                op_result.success, 
                op_result.duration_seconds
            )
        
        return ExecutionResult(
            success=failed == 0,
            operations_completed=completed,
            operations_failed=failed,
            duration_seconds=time.time() - batch_start_time,
            failed_operations=failed_ops
        )
    
    def _execute_single_operation(self, operation: Operation, options: RevertOptions) -> ExecutionResult:
        """
        Execute a single operation.
        
        Args:
            operation: Operation to execute
            options: Revert options
            
        Returns:
            ExecutionResult for the operation
        """
        logger.info(f"Executing operation {operation.id}: {operation.description}")
        
        # Create execution context
        context = ExecutionContext(operation_id=operation.id)
        self.execution_contexts[operation.id] = context
        
        context.status = ExecutionStatus.RUNNING
        context.start_time = time.time()
        
        try:
            if options.dry_run:
                # Simulate execution for dry run
                logger.info(f"DRY RUN: Would execute: {operation.command}")
                time.sleep(0.1)  # Simulate brief execution time
                context.status = ExecutionStatus.COMPLETED
                context.return_code = 0
                success = True
            else:
                # Execute actual command
                return_code, stdout, stderr = self.command_executor.execute_command(
                    operation.command,
                    timeout=operation.estimated_duration * 2  # Give extra time
                )
                
                context.return_code = return_code
                context.stdout = stdout
                context.stderr = stderr
                
                success = return_code == 0
                context.status = ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED
                
                if not success:
                    context.error_message = f"Command failed with return code {return_code}: {stderr}"
                    logger.error(f"Operation {operation.id} failed: {context.error_message}")
        
        except Exception as e:
            context.status = ExecutionStatus.FAILED
            context.error_message = str(e)
            success = False
            logger.error(f"Operation {operation.id} failed with exception: {e}")
        
        finally:
            context.end_time = time.time()
        
        return ExecutionResult(
            success=success,
            operations_completed=1 if success else 0,
            operations_failed=0 if success else 1,
            duration_seconds=context.duration,
            error_message=context.error_message,
            failed_operations=[] if success else [operation]
        )
    
    def cancel_execution(self):
        """Cancel ongoing execution."""
        logger.info("Cancelling execution")
        self.cancelled = True
        
        # Cancel running operations
        for context in self.execution_contexts.values():
            if context.is_running and context.process:
                try:
                    context.process.terminate()
                    context.cancelled = True
                    context.status = ExecutionStatus.CANCELLED
                except Exception as e:
                    logger.error(f"Failed to cancel operation {context.operation_id}: {e}")
    
    def get_execution_status(self, operation_id: str) -> Optional[ExecutionContext]:
        """
        Get execution status for a specific operation.
        
        Args:
            operation_id: ID of the operation
            
        Returns:
            ExecutionContext if found, None otherwise
        """
        return self.execution_contexts.get(operation_id)
    
    def cleanup(self):
        """Clean up execution resources."""
        logger.info("Cleaning up execution engine")
        self.cancel_execution()
        self._executor_pool.shutdown(wait=True)
        self.execution_contexts.clear()