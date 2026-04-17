"""
drift/revert/planner.py

Operation planning system that converts diff analysis into executable system operations.
Handles command generation, operation sequencing, dependency resolution, and batching.
"""
from __future__ import annotations
import logging
import platform
import shutil
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque

from drift.models import Change
from .models import Operation, OperationPlan, OperationBatch, RiskLevel, generate_operation_id
from .analyzer import RevertDiff
from .enhanced_change import EnhancedChange, enhance_diff_changes

logger = logging.getLogger("drift.revert.planner")


class CommandGenerator:
    """
    Generates system commands for different types of operations.
    
    Handles platform-specific command generation for packages, services,
    users, groups, and other system components.
    """
    
    def __init__(self):
        """Initialize the command generator."""
        self.platform = platform.system().lower()
        self.distro = self._detect_distribution()
        self.package_managers = self._detect_package_managers()
        
    def _detect_distribution(self) -> str:
        """Detect the Linux distribution."""
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('ID='):
                        return line.split('=')[1].strip().strip('"')
        except FileNotFoundError:
            pass
        
        # Fallback detection
        if shutil.which('apt'):
            return 'debian'
        elif shutil.which('yum') or shutil.which('dnf'):
            return 'rhel'
        else:
            return 'unknown'
    
    def _detect_package_managers(self) -> Dict[str, str]:
        """Detect available package managers and their commands."""
        managers = {}
        
        if shutil.which('apt'):
            managers['apt'] = 'apt'
        if shutil.which('apt-get'):
            managers['apt-get'] = 'apt-get'
        if shutil.which('yum'):
            managers['yum'] = 'yum'
        if shutil.which('dnf'):
            managers['dnf'] = 'dnf'
        if shutil.which('pip3'):
            managers['pip'] = 'pip3'
        if shutil.which('npm'):
            managers['npm'] = 'npm'
        if shutil.which('snap'):
            managers['snap'] = 'snap'
        if shutil.which('gem'):
            managers['gem'] = 'gem'
        
        return managers
    
    def generate_package_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate package management command.
        
        Args:
            change: The package change to process
            action: 'install', 'remove', or 'upgrade'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        if hasattr(change.before, 'manager'):
            manager = change.before.manager
        elif hasattr(change.after, 'manager'):
            manager = change.after.manager
        else:
            manager = 'apt'  # Default fallback
        
        package_name = change.name
        
        # Handle version-specific operations
        if action == 'install':
            if hasattr(change.after, 'version'):
                version = change.after.version
                if manager == 'apt':
                    command = f"apt-get install -y {package_name}={version}"
                    rollback = f"apt-get remove -y {package_name}"
                elif manager == 'yum':
                    command = f"yum install -y {package_name}-{version}"
                    rollback = f"yum remove -y {package_name}"
                elif manager == 'dnf':
                    command = f"dnf install -y {package_name}-{version}"
                    rollback = f"dnf remove -y {package_name}"
                elif manager == 'pip':
                    command = f"pip3 install {package_name}=={version}"
                    rollback = f"pip3 uninstall -y {package_name}"
                elif manager == 'npm':
                    command = f"npm install -g {package_name}@{version}"
                    rollback = f"npm uninstall -g {package_name}"
                elif manager == 'snap':
                    command = f"snap install {package_name}"
                    rollback = f"snap remove {package_name}"
                else:
                    command = f"# Unknown package manager: {manager}"
                    rollback = None
            else:
                # Install latest version
                if manager == 'apt':
                    command = f"apt-get install -y {package_name}"
                    rollback = f"apt-get remove -y {package_name}"
                elif manager == 'yum':
                    command = f"yum install -y {package_name}"
                    rollback = f"yum remove -y {package_name}"
                elif manager == 'dnf':
                    command = f"dnf install -y {package_name}"
                    rollback = f"dnf remove -y {package_name}"
                else:
                    command = f"# Install {package_name} with {manager}"
                    rollback = None
        
        elif action == 'remove':
            if manager == 'apt':
                command = f"apt-get remove -y {package_name}"
                if hasattr(change.before, 'version'):
                    rollback = f"apt-get install -y {package_name}={change.before.version}"
                else:
                    rollback = f"apt-get install -y {package_name}"
            elif manager == 'yum':
                command = f"yum remove -y {package_name}"
                rollback = f"yum install -y {package_name}"
            elif manager == 'dnf':
                command = f"dnf remove -y {package_name}"
                rollback = f"dnf install -y {package_name}"
            elif manager == 'pip':
                command = f"pip3 uninstall -y {package_name}"
                rollback = f"pip3 install {package_name}"
            elif manager == 'npm':
                command = f"npm uninstall -g {package_name}"
                rollback = f"npm install -g {package_name}"
            elif manager == 'snap':
                command = f"snap remove {package_name}"
                rollback = f"snap install {package_name}"
            else:
                command = f"# Remove {package_name} with {manager}"
                rollback = None
        
        elif action == 'upgrade' or action == 'downgrade':
            if hasattr(change.after, 'version'):
                target_version = change.after.version
                if manager == 'apt':
                    command = f"apt-get install -y {package_name}={target_version}"
                    if hasattr(change.before, 'version'):
                        rollback = f"apt-get install -y {package_name}={change.before.version}"
                    else:
                        rollback = f"apt-get remove -y {package_name}"
                elif manager == 'pip':
                    command = f"pip3 install {package_name}=={target_version}"
                    rollback = f"pip3 install {package_name}=={change.before.version}"
                else:
                    command = f"# Change {package_name} to {target_version} with {manager}"
                    rollback = None
            else:
                command = f"# Version change for {package_name}"
                rollback = None
        
        else:
            command = f"# Unknown package action: {action}"
            rollback = None
        
        return command, rollback
    
    def generate_service_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate service management command.
        
        Args:
            change: The service change to process
            action: 'start', 'stop', 'enable', 'disable', 'restart'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        service_name = change.name
        
        if action == 'start':
            command = f"systemctl start {service_name}"
            rollback = f"systemctl stop {service_name}"
        elif action == 'stop':
            command = f"systemctl stop {service_name}"
            rollback = f"systemctl start {service_name}"
        elif action == 'enable':
            command = f"systemctl enable {service_name}"
            rollback = f"systemctl disable {service_name}"
        elif action == 'disable':
            command = f"systemctl disable {service_name}"
            rollback = f"systemctl enable {service_name}"
        elif action == 'restart':
            command = f"systemctl restart {service_name}"
            rollback = None  # Restart doesn't have a direct rollback
        elif action == 'reload':
            command = f"systemctl reload {service_name}"
            rollback = None
        else:
            command = f"# Unknown service action: {action}"
            rollback = None
        
        return command, rollback
    
    def generate_user_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate user management command.
        
        Args:
            change: The user change to process
            action: 'create', 'delete', 'modify'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        username = change.name
        
        if action == 'create':
            if hasattr(change.after, 'uid') and hasattr(change.after, 'gid'):
                uid = change.after.uid
                gid = change.after.gid
                shell = getattr(change.after, 'shell', '/bin/bash')
                home = getattr(change.after, 'home', f'/home/{username}')
                
                command = f"useradd -u {uid} -g {gid} -s {shell} -d {home} -m {username}"
                rollback = f"userdel -r {username}"
            else:
                command = f"useradd -m {username}"
                rollback = f"userdel -r {username}"
        
        elif action == 'delete':
            command = f"userdel -r {username}"
            # User recreation is complex and risky
            rollback = f"# WARNING: User {username} deletion cannot be easily reversed"
        
        elif action == 'modify':
            # This is complex - would need to determine what specifically changed
            command = f"# Modify user {username} (specific changes need analysis)"
            rollback = f"# Rollback user {username} modifications"
        
        else:
            command = f"# Unknown user action: {action}"
            rollback = None
        
        return command, rollback
    
    def generate_group_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate group management command.
        
        Args:
            change: The group change to process
            action: 'create', 'delete', 'modify'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        groupname = change.name
        
        if action == 'create':
            if hasattr(change.after, 'gid'):
                gid = change.after.gid
                command = f"groupadd -g {gid} {groupname}"
            else:
                command = f"groupadd {groupname}"
            rollback = f"groupdel {groupname}"
        
        elif action == 'delete':
            command = f"groupdel {groupname}"
            if hasattr(change.before, 'gid'):
                rollback = f"groupadd -g {change.before.gid} {groupname}"
            else:
                rollback = f"groupadd {groupname}"
        
        elif action == 'modify':
            # Handle member changes
            command = f"# Modify group {groupname} (specific changes need analysis)"
            rollback = f"# Rollback group {groupname} modifications"
        
        else:
            command = f"# Unknown group action: {action}"
            rollback = None
        
        return command, rollback
    
    def generate_cron_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate cron job management command.
        
        Args:
            change: The cron change to process
            action: 'add', 'remove', 'modify'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        if action == 'add':
            if hasattr(change.after, 'owner') and hasattr(change.after, 'schedule') and hasattr(change.after, 'command'):
                owner = change.after.owner
                schedule = change.after.schedule
                cron_command = change.after.command
                
                # Add to user's crontab
                command = f'(crontab -u {owner} -l 2>/dev/null; echo "{schedule} {cron_command}") | crontab -u {owner} -'
                rollback = f'crontab -u {owner} -l | grep -v "{cron_command}" | crontab -u {owner} -'
            else:
                command = f"# Add cron job (details need analysis)"
                rollback = None
        
        elif action == 'remove':
            if hasattr(change.before, 'owner') and hasattr(change.before, 'command'):
                owner = change.before.owner
                cron_command = change.before.command
                
                command = f'crontab -u {owner} -l | grep -v "{cron_command}" | crontab -u {owner} -'
                rollback = f'(crontab -u {owner} -l 2>/dev/null; echo "{change.before.schedule} {cron_command}") | crontab -u {owner} -'
            else:
                command = f"# Remove cron job (details need analysis)"
                rollback = None
        
        else:
            command = f"# Unknown cron action: {action}"
            rollback = None
        
        return command, rollback
    
    def generate_sysctl_command(self, change: Change, action: str) -> Tuple[str, Optional[str]]:
        """
        Generate sysctl parameter command.
        
        Args:
            change: The sysctl change to process
            action: 'set', 'unset'
            
        Returns:
            Tuple of (command, rollback_command)
        """
        param_name = change.name
        
        if action == 'set':
            if hasattr(change.after, 'value'):
                value = change.after.value
                command = f"sysctl -w {param_name}={value}"
                if hasattr(change.before, 'value'):
                    rollback = f"sysctl -w {param_name}={change.before.value}"
                else:
                    rollback = f"# Restore original {param_name} value"
            else:
                command = f"# Set sysctl {param_name}"
                rollback = None
        
        elif action == 'unset':
            # Sysctl parameters can't really be "unset", only changed
            if hasattr(change.before, 'value'):
                rollback = f"sysctl -w {param_name}={change.before.value}"
            else:
                rollback = f"# Restore sysctl {param_name}"
            command = f"# Unset sysctl {param_name} (restore to default)"
        
        else:
            command = f"# Unknown sysctl action: {action}"
            rollback = None
        
        return command, rollback


class OperationPlanner:
    """
    Main operation planner that converts diff analysis into executable operation plans.
    
    Handles operation sequencing, dependency resolution, batching, and validation.
    """
    
    def __init__(self):
        """Initialize the operation planner."""
        self.command_generator = CommandGenerator()
        self.logger = logging.getLogger("drift.revert.planner.OperationPlanner")
    
    def plan_operations(self, revert_diff: RevertDiff, options: Optional[Dict] = None) -> OperationPlan:
        """
        Convert revert diff analysis into a complete operation plan.
        
        Args:
            revert_diff: Enhanced diff analysis from DiffAnalyzer
            options: Optional planning options
            
        Returns:
            Complete operation plan with sequenced batches
        """
        if options is None:
            options = {}
        
        self.logger.info(f"Planning operations for {len(revert_diff.changes)} changes")
        
        # Convert changes to operations
        operations = self._convert_changes_to_operations(revert_diff)
        
        # Filter out excluded categories
        excluded_categories = options.get('exclude_categories', set())
        if excluded_categories:
            operations = [op for op in operations if op.category not in excluded_categories]
            self.logger.info(f"Filtered out {len(excluded_categories)} categories, {len(operations)} operations remaining")
        
        # Sequence operations by dependencies
        sequenced_batches = self.sequence_operations(operations)
        
        # Validate the operation plan
        validation_result = self.validate_dependencies(OperationPlan(sequenced_batches))
        
        if not validation_result['valid']:
            self.logger.warning(f"Operation plan validation failed: {validation_result['errors']}")
        
        plan = OperationPlan(sequenced_batches)
        
        self.logger.info(f"Operation plan created: {plan.total_operations} operations in {len(plan.batches)} batches")
        
        return plan
    
    def _convert_changes_to_operations(self, revert_diff: RevertDiff) -> List[Operation]:
        """Convert analyzed changes into executable operations."""
        operations = []
        
        for category, changes in revert_diff.categorized_changes.items():
            for change in changes:
                operation = self._create_operation_for_change(change, category)
                if operation:
                    operations.append(operation)
        
        return operations
    
    def _create_operation_for_change(self, change: Change, category: str) -> Optional[Operation]:
        """Create a single operation from a change."""
        # Determine the action needed to revert this change
        action = self._determine_revert_action(change)
        
        if not action:
            self.logger.warning(f"Could not determine action for {category} change: {change.name}")
            return None
        
        # Generate the command
        command, rollback_command = self._generate_command_for_change(change, category, action)
        
        if not command or command.startswith('#'):
            self.logger.warning(f"Could not generate command for {category}/{action}: {change.name}")
            return None
        
        # Determine risk level
        risk_level = self._assess_operation_risk(change, category, action)
        
        # Create the operation
        operation = Operation(
            id=generate_operation_id(category, action, change.name),
            category=category,
            action=action,
            target=change.name,
            command=command,
            risk_level=risk_level,
            rollback_command=rollback_command,
            estimated_duration=self._estimate_operation_duration(category, action),
            description=f"{action.title()} {category} '{change.name}'"
        )
        
        return operation
    
    def _determine_revert_action(self, change: Change) -> Optional[str]:
        """Determine what action is needed to revert a change."""
        if change.kind == "added":
            # Something was added, so we need to remove it
            if change.category == "package":
                return "remove"
            elif change.category == "service":
                return "stop"  # Stop the service that was started
            elif change.category == "user":
                return "delete"
            elif change.category == "group":
                return "delete"
            elif change.category == "cron":
                return "remove"
            else:
                return "remove"
        
        elif change.kind == "removed":
            # Something was removed, so we need to add it back
            if change.category == "package":
                return "install"
            elif change.category == "service":
                return "start"  # Start the service that was stopped
            elif change.category == "user":
                return "create"
            elif change.category == "group":
                return "create"
            elif change.category == "cron":
                return "add"
            else:
                return "add"
        
        elif change.kind == "modified":
            # Something was modified, so we need to change it back
            if change.category == "package":
                return "downgrade"  # Change to previous version
            elif change.category == "service":
                return "reconfigure"  # Change service state
            elif change.category == "user":
                return "modify"
            elif change.category == "group":
                return "modify"
            elif change.category == "sysctl":
                return "set"
            else:
                return "modify"
        
        return None
    
    def _generate_command_for_change(self, change: Change, category: str, action: str) -> Tuple[str, Optional[str]]:
        """Generate the system command for a change."""
        if category == "package":
            return self.command_generator.generate_package_command(change, action)
        elif category == "service":
            return self.command_generator.generate_service_command(change, action)
        elif category == "user":
            return self.command_generator.generate_user_command(change, action)
        elif category == "group":
            return self.command_generator.generate_group_command(change, action)
        elif category == "cron":
            return self.command_generator.generate_cron_command(change, action)
        elif category == "sysctl":
            return self.command_generator.generate_sysctl_command(change, action)
        else:
            return f"# Unsupported category: {category}", None
    
    def _assess_operation_risk(self, change: Change, category: str, action: str) -> RiskLevel:
        """Assess the risk level of an operation."""
        # Use existing critical flag as a starting point
        if change.critical:
            return RiskLevel.HIGH
        
        # Category and action specific risk assessment
        if category == "user" and action in ["delete", "create"]:
            return RiskLevel.CRITICAL
        elif category == "service" and action in ["stop", "start"]:
            return RiskLevel.HIGH
        elif category == "package" and action in ["remove", "install"]:
            return RiskLevel.MEDIUM
        elif category == "sysctl":
            return RiskLevel.HIGH
        else:
            return RiskLevel.LOW
    
    def _estimate_operation_duration(self, category: str, action: str) -> int:
        """Estimate duration in seconds for an operation."""
        duration_map = {
            "package": {"install": 120, "remove": 60, "downgrade": 90},
            "service": {"start": 10, "stop": 10, "restart": 15},
            "user": {"create": 5, "delete": 5, "modify": 3},
            "group": {"create": 2, "delete": 2, "modify": 2},
            "cron": {"add": 2, "remove": 2},
            "sysctl": {"set": 1}
        }
        
        category_durations = duration_map.get(category, {})
        return category_durations.get(action, 10)  # Default 10 seconds
    
    def sequence_operations(self, operations: List[Operation]) -> List[OperationBatch]:
        """
        Sequence operations into batches based on dependencies.
        
        Args:
            operations: List of operations to sequence
            
        Returns:
            List of operation batches in execution order
        """
        self.logger.info(f"Sequencing {len(operations)} operations")
        
        # Group operations by category for dependency-based sequencing
        by_category = defaultdict(list)
        for op in operations:
            by_category[op.category].append(op)
        
        # Define execution order based on dependencies
        # Users and groups first, then packages, then services
        execution_order = [
            "user",
            "group", 
            "package",
            "service",
            "cron",
            "sysctl",
            "mount",
            "env_var",
            "kernel_module"
        ]
        
        batches = []
        batch_id = 1
        
        for category in execution_order:
            if category in by_category:
                category_ops = by_category[category]
                
                # For most categories, we can batch operations together
                # But some need to be sequential
                if category in ["service", "user"]:
                    # Services and users should be done one at a time for safety
                    for op in category_ops:
                        batch = OperationBatch(
                            id=f"batch_{batch_id}",
                            operations=[op]
                        )
                        batches.append(batch)
                        batch_id += 1
                else:
                    # Other categories can be batched together
                    if category_ops:
                        batch = OperationBatch(
                            id=f"batch_{batch_id}",
                            operations=category_ops
                        )
                        batches.append(batch)
                        batch_id += 1
        
        # Handle any remaining categories not in the execution order
        remaining_categories = set(by_category.keys()) - set(execution_order)
        for category in remaining_categories:
            category_ops = by_category[category]
            if category_ops:
                batch = OperationBatch(
                    id=f"batch_{batch_id}",
                    operations=category_ops
                )
                batches.append(batch)
                batch_id += 1
        
        self.logger.info(f"Created {len(batches)} operation batches")
        
        return batches
    
    def validate_dependencies(self, plan: OperationPlan) -> Dict:
        """
        Validate that the operation plan respects all dependencies.
        
        Args:
            plan: Operation plan to validate
            
        Returns:
            Dictionary with validation results
        """
        errors = []
        warnings = []
        
        # Check for circular dependencies
        all_operations = []
        for batch in plan.batches:
            all_operations.extend(batch.operations)
        
        # Build dependency graph
        dep_graph = {}
        for op in all_operations:
            dep_graph[op.id] = op.dependencies
        
        # Check for cycles using DFS
        visited = set()
        rec_stack = set()
        
        def has_cycle(node):
            if node in rec_stack:
                return True
            if node in visited:
                return False
            
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in dep_graph.get(node, []):
                if has_cycle(neighbor):
                    return True
            
            rec_stack.remove(node)
            return False
        
        for op_id in dep_graph:
            if op_id not in visited:
                if has_cycle(op_id):
                    errors.append(f"Circular dependency detected involving operation {op_id}")
        
        # Check that dependencies are satisfied by execution order
        executed_ops = set()
        for batch in plan.batches:
            for op in batch.operations:
                # Check if all dependencies have been executed
                for dep_id in op.dependencies:
                    if dep_id not in executed_ops:
                        errors.append(f"Operation {op.id} depends on {dep_id} which hasn't been executed yet")
                
                executed_ops.add(op.id)
        
        # Check for high-risk operations without confirmation
        high_risk_ops = [op for op in all_operations if op.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]]
        if high_risk_ops:
            warnings.append(f"Plan contains {len(high_risk_ops)} high-risk operations that may require confirmation")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'high_risk_operations': len(high_risk_ops),
            'total_operations': len(all_operations)
        }