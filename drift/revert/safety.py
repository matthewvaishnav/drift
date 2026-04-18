"""
drift/revert/safety.py

Safety validation system for revert operations.
Provides comprehensive safety checks, risk assessment, user confirmation workflows,
and safety backup management to ensure revert operations are executed safely.
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from drift.collectors import run_all
from drift.storage import save_snapshot

from .models import OperationPlan, SafetyAssessment, Risk, RiskLevel, RevertOptions

logger = logging.getLogger("drift.revert.safety")


class SystemValidator:
    """
    Validates system prerequisites for revert operations.
    
    Checks disk space, network connectivity, system resources,
    and other prerequisites needed for safe revert execution.
    """
    
    def __init__(self):
        """Initialize the system validator."""
        self.min_disk_space_mb = 1024  # 1GB minimum
        self.min_memory_mb = 512       # 512MB minimum
        self.required_commands = ['systemctl', 'which', 'id']
    
    def validate_disk_space(self, required_space_mb: int = None) -> Tuple[bool, str]:
        """
        Validate available disk space.
        
        Args:
            required_space_mb: Required space in MB (uses default if None)
            
        Returns:
            Tuple of (sufficient_space, message)
        """
        if required_space_mb is None:
            required_space_mb = self.min_disk_space_mb
        
        try:
            # Check available space in root filesystem
            statvfs = os.statvfs('/')
            available_bytes = statvfs.f_bavail * statvfs.f_frsize
            available_mb = available_bytes // (1024 * 1024)
            
            if available_mb >= required_space_mb:
                return True, f"Sufficient disk space: {available_mb}MB available"
            else:
                return False, f"Insufficient disk space: {available_mb}MB available, {required_space_mb}MB required"
        
        except Exception as e:
            return False, f"Could not check disk space: {e}"
    
    def validate_memory(self) -> Tuple[bool, str]:
        """
        Validate available memory.
        
        Returns:
            Tuple of (sufficient_memory, message)
        """
        try:
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        available_kb = int(line.split()[1])
                        available_mb = available_kb // 1024
                        
                        if available_mb >= self.min_memory_mb:
                            return True, f"Sufficient memory: {available_mb}MB available"
                        else:
                            return False, f"Low memory: {available_mb}MB available, {self.min_memory_mb}MB recommended"
        
        except Exception as e:
            return False, f"Could not check memory: {e}"
        
        return False, "Could not determine memory status"
    
    def validate_network_connectivity(self) -> Tuple[bool, str]:
        """
        Validate network connectivity for package operations.
        
        Returns:
            Tuple of (has_connectivity, message)
        """
        try:
            # Try to resolve a common DNS name
            result = subprocess.run(
                ['nslookup', 'google.com'],
                capture_output=True,
                timeout=10,
                text=True
            )
            
            if result.returncode == 0:
                return True, "Network connectivity available"
            else:
                return False, "DNS resolution failed - limited network connectivity"
        
        except subprocess.TimeoutExpired:
            return False, "Network connectivity check timed out"
        except FileNotFoundError:
            # nslookup not available, try ping
            try:
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '5', '8.8.8.8'],
                    capture_output=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    return True, "Network connectivity available (ping successful)"
                else:
                    return False, "Network ping failed - no connectivity"
            
            except Exception:
                return False, "Could not verify network connectivity"
        
        except Exception as e:
            return False, f"Network connectivity check failed: {e}"
    
    def validate_privileges(self) -> Tuple[bool, str]:
        """
        Validate that we have sufficient privileges for system operations.
        
        Returns:
            Tuple of (has_privileges, message)
        """
        try:
            # Check if running as root
            if os.geteuid() == 0:
                return True, "Running as root - full privileges available"
            
            # Check if user can sudo
            result = subprocess.run(
                ['sudo', '-n', 'true'],
                capture_output=True,
                timeout=5
            )
            
            if result.returncode == 0:
                return True, "Sudo privileges available"
            else:
                return False, "Insufficient privileges - root or sudo access required"
        
        except Exception as e:
            return False, f"Could not verify privileges: {e}"
    
    def validate_required_commands(self) -> Tuple[bool, str]:
        """
        Validate that required system commands are available.
        
        Returns:
            Tuple of (commands_available, message)
        """
        missing_commands = []
        
        for command in self.required_commands:
            if not shutil.which(command):
                missing_commands.append(command)
        
        if not missing_commands:
            return True, "All required commands available"
        else:
            return False, f"Missing required commands: {', '.join(missing_commands)}"
    
    def validate_system_load(self) -> Tuple[bool, str]:
        """
        Validate system load to ensure it's safe to perform operations.
        
        Returns:
            Tuple of (load_acceptable, message)
        """
        try:
            # Check system load average
            load1, load5, load15 = os.getloadavg()
            
            # Get number of CPU cores
            cpu_count = os.cpu_count() or 1
            
            # Consider load acceptable if 1-minute average is below 2x CPU count
            max_acceptable_load = cpu_count * 2.0
            
            if load1 <= max_acceptable_load:
                return True, f"System load acceptable: {load1:.2f} (max: {max_acceptable_load})"
            else:
                return False, f"High system load: {load1:.2f} (max recommended: {max_acceptable_load})"
        
        except Exception as e:
            return False, f"Could not check system load: {e}"


class RiskAssessor:
    """
    Assesses risks for revert operations and provides mitigation recommendations.
    
    Analyzes operation plans to identify potential risks and provides
    recommendations for safe execution.
    """
    
    def __init__(self):
        """Initialize the risk assessor."""
        self.critical_services = {
            'sshd', 'systemd', 'networkd', 'dbus', 'systemd-logind',
            'systemd-resolved', 'systemd-networkd', 'network-manager'
        }
        
        self.critical_packages = {
            'systemd', 'kernel', 'glibc', 'bash', 'coreutils',
            'openssh-server', 'network-manager', 'dbus'
        }
        
        self.critical_users = {
            'root', 'daemon', 'bin', 'sys', 'sync', 'games',
            'man', 'lp', 'mail', 'news', 'uucp', 'proxy'
        }
    
    def assess_operation_risks(self, plan: OperationPlan) -> List[Risk]:
        """
        Assess risks for all operations in a plan.
        
        Args:
            plan: Operation plan to assess
            
        Returns:
            List of identified risks
        """
        risks = []
        
        # Analyze each operation
        for batch in plan.batches:
            for operation in batch.operations:
                operation_risks = self._assess_single_operation_risk(operation)
                risks.extend(operation_risks)
        
        # Analyze plan-level risks
        plan_risks = self._assess_plan_level_risks(plan)
        risks.extend(plan_risks)
        
        return risks
    
    def _assess_single_operation_risk(self, operation) -> List[Risk]:
        """Assess risks for a single operation."""
        risks = []
        
        # Category-specific risk assessment
        if operation.category == 'service':
            if operation.target in self.critical_services:
                risks.append(Risk(
                    level=RiskLevel.CRITICAL,
                    message=f"Critical system service operation: {operation.target}",
                    operation=operation,
                    mitigation="Ensure SSH access through alternative means before proceeding"
                ))
            elif operation.action in ['stop', 'disable']:
                risks.append(Risk(
                    level=RiskLevel.HIGH,
                    message=f"Service {operation.target} will be stopped/disabled",
                    operation=operation,
                    mitigation="Verify service is not critical for system operation"
                ))
        
        elif operation.category == 'package':
            if any(critical in operation.target.lower() for critical in self.critical_packages):
                risks.append(Risk(
                    level=RiskLevel.CRITICAL,
                    message=f"Critical system package operation: {operation.target}",
                    operation=operation,
                    mitigation="Create full system backup before proceeding"
                ))
            elif operation.action == 'remove':
                risks.append(Risk(
                    level=RiskLevel.MEDIUM,
                    message=f"Package {operation.target} will be removed",
                    operation=operation,
                    mitigation="Verify no critical services depend on this package"
                ))
        
        elif operation.category == 'user':
            if operation.target in self.critical_users:
                risks.append(Risk(
                    level=RiskLevel.CRITICAL,
                    message=f"Critical system user operation: {operation.target}",
                    operation=operation,
                    mitigation="Do not modify critical system users"
                ))
            elif operation.action == 'delete':
                risks.append(Risk(
                    level=RiskLevel.HIGH,
                    message=f"User {operation.target} will be deleted",
                    operation=operation,
                    mitigation="Ensure user is not currently logged in and has no critical processes"
                ))
        
        elif operation.category == 'sysctl':
            security_params = {
                'kernel.randomize_va_space', 'net.ipv4.ip_forward',
                'kernel.dmesg_restrict', 'kernel.kptr_restrict'
            }
            if operation.target in security_params:
                risks.append(Risk(
                    level=RiskLevel.HIGH,
                    message=f"Security-related sysctl parameter: {operation.target}",
                    operation=operation,
                    mitigation="Verify security implications of parameter change"
                ))
        
        return risks
    
    def _assess_plan_level_risks(self, plan: OperationPlan) -> List[Risk]:
        """Assess risks at the plan level."""
        risks = []
        
        # Check for high number of operations
        if plan.total_operations > 20:
            risks.append(Risk(
                level=RiskLevel.MEDIUM,
                message=f"Large number of operations: {plan.total_operations}",
                mitigation="Consider breaking into smaller batches or excluding non-critical categories"
            ))
        
        # Check for long execution time
        if plan.estimated_duration > 600:  # 10 minutes
            risks.append(Risk(
                level=RiskLevel.MEDIUM,
                message=f"Long execution time: {plan.estimated_duration}s ({plan.estimated_duration//60}m)",
                mitigation="Ensure stable network connection and sufficient time for completion"
            ))
        
        # Check for mixed high-risk operations
        high_risk_ops = sum(1 for batch in plan.batches for op in batch.operations 
                           if op.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL])
        
        if high_risk_ops > 5:
            risks.append(Risk(
                level=RiskLevel.HIGH,
                message=f"Multiple high-risk operations: {high_risk_ops}",
                mitigation="Review each high-risk operation carefully and consider manual execution"
            ))
        
        return risks


class SafetyBackupManager:
    """
    Manages safety backups for revert operations.
    
    Creates, verifies, and manages safety snapshots that can be used
    for recovery if revert operations fail.
    """
    
    def __init__(self):
        """Initialize the backup manager."""
        self.backup_retention_days = 7
        self.max_backups = 10
    
    def create_safety_backup(self, reason: str = "revert_safety") -> Optional[str]:
        """
        Create a safety backup snapshot.
        
        Args:
            reason: Reason for creating the backup
            
        Returns:
            Backup snapshot hash if successful, None if failed
        """
        try:
            logger.info(f"Creating safety backup: {reason}")
            
            # Take a complete system snapshot
            snapshot = run_all()
            
            # Add backup metadata
            snapshot.errors.append(f"Safety backup created for: {reason}")
            
            # Save the snapshot
            backup_hash = save_snapshot(snapshot)
            
            logger.info(f"Safety backup created: {backup_hash}")
            return backup_hash
        
        except Exception as e:
            logger.error(f"Failed to create safety backup: {e}")
            return None
    
    def verify_backup_integrity(self, backup_hash: str) -> Tuple[bool, str]:
        """
        Verify the integrity of a backup snapshot.
        
        Args:
            backup_hash: Hash of the backup to verify
            
        Returns:
            Tuple of (is_valid, message)
        """
        try:
            from drift.storage import load_snapshot
            
            # Try to load the snapshot
            snapshot = load_snapshot(backup_hash)
            
            if snapshot is None:
                return False, f"Backup {backup_hash} could not be loaded"
            
            # Basic integrity checks
            if not snapshot.hostname:
                return False, "Backup missing hostname"
            
            if not snapshot.timestamp:
                return False, "Backup missing timestamp"
            
            # Check if snapshot has reasonable content
            total_items = (len(snapshot.packages) + len(snapshot.services) + 
                          len(snapshot.users) + len(snapshot.groups))
            
            if total_items == 0:
                return False, "Backup appears to be empty"
            
            return True, f"Backup {backup_hash} is valid ({total_items} items)"
        
        except Exception as e:
            return False, f"Backup verification failed: {e}"
    
    def cleanup_old_backups(self) -> int:
        """
        Clean up old safety backups based on retention policy.
        
        Returns:
            Number of backups cleaned up
        """
        try:
            from drift.storage import read_log
            import datetime
            
            # Get all commits (which include backups)
            commits = read_log()
            
            # Filter for safety backups older than retention period
            cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=self.backup_retention_days)
            
            old_backups = []
            for commit in commits:
                if "Safety backup" in commit.message:
                    commit_date = datetime.datetime.fromisoformat(commit.timestamp.replace('Z', '+00:00'))
                    if commit_date < cutoff_date:
                        old_backups.append(commit)
            
            # Keep at least some recent backups even if they're old
            if len(old_backups) > self.max_backups:
                old_backups = old_backups[self.max_backups:]
            
            # TODO: Implement actual backup deletion
            # For now, just log what would be deleted
            logger.info(f"Would clean up {len(old_backups)} old safety backups")
            
            return len(old_backups)
        
        except Exception as e:
            logger.error(f"Backup cleanup failed: {e}")
            return 0


class UserConfirmationManager:
    """
    Manages user confirmation workflows for high-risk operations.
    
    Provides interactive confirmation prompts and handles user responses
    for operations that require explicit approval.
    """
    
    def __init__(self):
        """Initialize the confirmation manager."""
        self.confirmation_timeout = 300  # 5 minutes
    
    def request_confirmation(self, risks: List[Risk], plan: OperationPlan) -> bool:
        """
        Request user confirmation for risky operations.
        
        Args:
            risks: List of identified risks
            plan: Operation plan to confirm
            
        Returns:
            True if user confirms, False otherwise
        """
        try:
            # Display risk summary
            self._display_risk_summary(risks, plan)
            
            # Get user input
            response = self._get_user_response()
            
            return response.lower() in ['y', 'yes', 'confirm']
        
        except KeyboardInterrupt:
            print("\nOperation cancelled by user")
            return False
        except Exception as e:
            logger.error(f"Confirmation request failed: {e}")
            return False
    
    def _display_risk_summary(self, risks: List[Risk], plan: OperationPlan):
        """Display a summary of risks to the user."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich import box
            
            console = Console()
            
            # Create risk summary
            console.print("\n[bold red]⚠️  REVERT OPERATION SAFETY REVIEW[/bold red]")
            
            # Operation summary
            summary_table = Table(box=box.SIMPLE)
            summary_table.add_column("Metric", style="cyan")
            summary_table.add_column("Value", style="white")
            
            summary_table.add_row("Total Operations", str(plan.total_operations))
            summary_table.add_row("Execution Batches", str(len(plan.batches)))
            summary_table.add_row("Estimated Duration", f"{plan.estimated_duration}s ({plan.estimated_duration//60}m {plan.estimated_duration%60}s)")
            summary_table.add_row("Overall Risk Level", plan.risk_assessment.value.upper())
            
            console.print(Panel(summary_table, title="Operation Summary"))
            
            # Risk details
            if risks:
                risk_table = Table(box=box.SIMPLE)
                risk_table.add_column("Risk Level", style="bold")
                risk_table.add_column("Description", style="white")
                risk_table.add_column("Mitigation", style="dim")
                
                for risk in risks:
                    level_color = {
                        RiskLevel.CRITICAL: "red",
                        RiskLevel.HIGH: "yellow", 
                        RiskLevel.MEDIUM: "blue",
                        RiskLevel.LOW: "green"
                    }.get(risk.level, "white")
                    
                    risk_table.add_row(
                        f"[{level_color}]{risk.level.value.upper()}[/{level_color}]",
                        risk.message,
                        risk.mitigation or "No specific mitigation"
                    )
                
                console.print(Panel(risk_table, title="Identified Risks"))
            
            # High-risk operations
            high_risk_ops = [op for batch in plan.batches for op in batch.operations 
                           if op.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]]
            
            if high_risk_ops:
                ops_table = Table(box=box.SIMPLE)
                ops_table.add_column("Category", style="cyan")
                ops_table.add_column("Operation", style="white")
                ops_table.add_column("Target", style="yellow")
                ops_table.add_column("Risk", style="bold")
                
                for op in high_risk_ops[:10]:  # Show first 10
                    risk_color = "red" if op.risk_level == RiskLevel.CRITICAL else "yellow"
                    ops_table.add_row(
                        op.category.upper(),
                        op.action,
                        op.target,
                        f"[{risk_color}]{op.risk_level.value.upper()}[/{risk_color}]"
                    )
                
                if len(high_risk_ops) > 10:
                    ops_table.add_row("...", f"and {len(high_risk_ops) - 10} more", "", "")
                
                console.print(Panel(ops_table, title="High-Risk Operations"))
        
        except ImportError:
            # Fallback for systems without rich
            print("\n⚠️  REVERT OPERATION SAFETY REVIEW")
            print("=" * 50)
            print(f"Total Operations: {plan.total_operations}")
            print(f"Estimated Duration: {plan.estimated_duration}s")
            print(f"Risk Level: {plan.risk_assessment.value.upper()}")
            
            if risks:
                print(f"\nIdentified Risks: {len(risks)}")
                for risk in risks[:5]:  # Show first 5
                    print(f"  - {risk.level.value.upper()}: {risk.message}")
    
    def _get_user_response(self) -> str:
        """Get user confirmation response."""
        try:
            from rich.console import Console
            console = Console()
            
            console.print("\n[bold yellow]Do you want to proceed with this revert operation?[/bold yellow]")
            console.print("[dim]Type 'yes' or 'confirm' to proceed, anything else to cancel:[/dim]")
            
            response = input("> ").strip()
            return response
        
        except ImportError:
            print("\nDo you want to proceed with this revert operation?")
            print("Type 'yes' or 'confirm' to proceed, anything else to cancel:")
            
            response = input("> ").strip()
            return response


class SafetyValidator:
    """
    Main safety validator that coordinates all safety checks.
    
    Provides comprehensive safety validation including system prerequisites,
    risk assessment, backup creation, and user confirmation workflows.
    """
    
    def __init__(self):
        """Initialize the safety validator."""
        self.system_validator = SystemValidator()
        self.risk_assessor = RiskAssessor()
        self.backup_manager = SafetyBackupManager()
        self.confirmation_manager = UserConfirmationManager()
    
    def assess_safety(self, plan: OperationPlan, options: RevertOptions) -> SafetyAssessment:
        """
        Perform comprehensive safety assessment for a revert operation.
        
        Args:
            plan: Operation plan to assess
            options: Revert options
            
        Returns:
            Complete safety assessment
        """
        logger.info(f"Performing safety assessment for {plan.total_operations} operations")
        
        risks = []
        prerequisites_met = True
        
        # System prerequisite validation
        prereq_results = self._validate_prerequisites(plan)
        if not prereq_results['all_passed']:
            prerequisites_met = False
            for check, result in prereq_results['checks'].items():
                if not result['passed']:
                    risks.append(Risk(
                        level=RiskLevel.HIGH,
                        message=f"Prerequisite check failed: {check}",
                        mitigation=result['message']
                    ))
        
        # Risk assessment
        operation_risks = self.risk_assessor.assess_operation_risks(plan)
        risks.extend(operation_risks)
        
        # Determine overall safety
        critical_risks = [r for r in risks if r.level == RiskLevel.CRITICAL]
        high_risks = [r for r in risks if r.level == RiskLevel.HIGH]
        
        safe = (len(critical_risks) == 0 and 
                (len(high_risks) == 0 or options.force) and
                prerequisites_met)
        
        # Determine if confirmation is required
        requires_confirmation = (
            (len(critical_risks) > 0 or len(high_risks) > 0) and
            not options.skip_confirmation and
            not options.dry_run
        )
        
        # Generate recommendations
        recommendations = self._generate_recommendations(risks, plan)
        
        assessment = SafetyAssessment(
            safe=safe,
            risks=risks,
            requires_confirmation=requires_confirmation,
            recommended_actions=recommendations,
            backup_required=not options.dry_run and len(high_risks) > 0,
            prerequisites_met=prerequisites_met
        )
        
        logger.info(f"Safety assessment complete: safe={safe}, risks={len(risks)}, confirmation_required={requires_confirmation}")
        
        return assessment
    
    def _validate_prerequisites(self, plan: OperationPlan) -> Dict:
        """Validate system prerequisites."""
        checks = {}
        
        # Estimate required disk space (rough calculation)
        package_ops = sum(1 for batch in plan.batches for op in batch.operations if op.category == 'package')
        estimated_space_mb = package_ops * 100  # 100MB per package operation
        
        # Run all prerequisite checks
        checks['disk_space'] = self._run_check(
            self.system_validator.validate_disk_space, estimated_space_mb
        )
        checks['memory'] = self._run_check(
            self.system_validator.validate_memory
        )
        checks['network'] = self._run_check(
            self.system_validator.validate_network_connectivity
        )
        checks['privileges'] = self._run_check(
            self.system_validator.validate_privileges
        )
        checks['commands'] = self._run_check(
            self.system_validator.validate_required_commands
        )
        checks['system_load'] = self._run_check(
            self.system_validator.validate_system_load
        )
        
        all_passed = all(check['passed'] for check in checks.values())
        
        return {
            'all_passed': all_passed,
            'checks': checks
        }
    
    def _run_check(self, check_func, *args) -> Dict:
        """Run a single prerequisite check."""
        try:
            passed, message = check_func(*args)
            return {'passed': passed, 'message': message}
        except Exception as e:
            return {'passed': False, 'message': f"Check failed: {e}"}
    
    def _generate_recommendations(self, risks: List[Risk], plan: OperationPlan) -> List[str]:
        """Generate safety recommendations based on risks."""
        recommendations = []
        
        # Risk-based recommendations
        critical_risks = [r for r in risks if r.level == RiskLevel.CRITICAL]
        high_risks = [r for r in risks if r.level == RiskLevel.HIGH]
        
        if critical_risks:
            recommendations.append("CRITICAL: Review all critical risks before proceeding")
            recommendations.append("Consider manual execution of critical operations")
        
        if high_risks:
            recommendations.append("Create safety backup before proceeding")
            recommendations.append("Ensure alternative access methods are available")
        
        # Plan-based recommendations
        if plan.total_operations > 10:
            recommendations.append("Consider breaking large operation plan into smaller batches")
        
        if plan.estimated_duration > 300:  # 5 minutes
            recommendations.append("Ensure stable network connection for long-running operations")
        
        # Category-specific recommendations
        has_service_ops = any(op.category == 'service' for batch in plan.batches for op in batch.operations)
        has_user_ops = any(op.category == 'user' for batch in plan.batches for op in batch.operations)
        
        if has_service_ops:
            recommendations.append("Verify service dependencies before stopping services")
        
        if has_user_ops:
            recommendations.append("Ensure no users are currently logged in before user operations")
        
        return recommendations
    
    def create_safety_backup(self) -> Optional[str]:
        """Create a safety backup snapshot."""
        return self.backup_manager.create_safety_backup("pre_revert_safety")
    
    def request_user_confirmation(self, risks: List[Risk], plan: OperationPlan) -> bool:
        """Request user confirmation for risky operations."""
        return self.confirmation_manager.request_confirmation(risks, plan)