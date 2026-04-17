"""
drift/revert/enhanced_change.py

Enhanced change detection and modeling for revert operations.
Extends the basic Change model with revert-specific fields and analysis.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set
from enum import Enum

from drift.models import Change as BaseChange
from .models import RiskLevel, generate_operation_id


class OperationFeasibility(Enum):
    """Feasibility levels for revert operations."""
    FEASIBLE = "feasible"
    RISKY = "risky"
    INFEASIBLE = "infeasible"


@dataclass
class EnhancedChange:
    """
    Enhanced change model with revert-specific analysis.
    
    Extends the basic Change model with additional fields for risk assessment,
    dependency tracking, and operation feasibility validation.
    """
    # Core change information (from base Change model)
    category: str
    kind: str  # "added", "removed", "modified"
    name: str
    before: Any
    after: Any
    critical: bool = False
    
    # Revert-specific enhancements
    risk_level: RiskLevel = RiskLevel.LOW
    operation_id: str = ""
    dependencies: List[str] = field(default_factory=list)
    feasibility: OperationFeasibility = OperationFeasibility.FEASIBLE
    feasibility_reason: Optional[str] = None
    estimated_duration: int = 30  # seconds
    requires_confirmation: bool = False
    rollback_complexity: RiskLevel = RiskLevel.LOW
    
    # Operation-specific metadata
    operation_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize computed fields after creation."""
        if not self.operation_id:
            self.operation_id = generate_operation_id(self.category, self.kind, self.name)
        
        # Set requires_confirmation based on risk level
        if self.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            self.requires_confirmation = True
    
    @classmethod
    def from_base_change(cls, base_change: BaseChange) -> "EnhancedChange":
        """Create an EnhancedChange from a base Change object."""
        return cls(
            category=base_change.category,
            kind=base_change.kind,
            name=base_change.name,
            before=base_change.before,
            after=base_change.after,
            critical=base_change.critical
        )
    
    def to_base_change(self) -> BaseChange:
        """Convert back to a base Change object."""
        return BaseChange(
            category=self.category,
            kind=self.kind,
            name=self.name,
            before=self.before,
            after=self.after,
            critical=self.critical
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        result["risk_level"] = self.risk_level.value
        result["feasibility"] = self.feasibility.value
        result["rollback_complexity"] = self.rollback_complexity.value
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnhancedChange":
        """Create from dictionary."""
        data = data.copy()
        if "risk_level" in data:
            data["risk_level"] = RiskLevel(data["risk_level"])
        if "feasibility" in data:
            data["feasibility"] = OperationFeasibility(data["feasibility"])
        if "rollback_complexity" in data:
            data["rollback_complexity"] = RiskLevel(data["rollback_complexity"])
        return cls(**data)


class ChangeEnhancer:
    """
    Enhances basic Change objects with revert-specific analysis.
    
    Provides risk assessment, dependency detection, and feasibility validation
    for individual changes in the context of revert operations.
    """
    
    def __init__(self):
        """Initialize the change enhancer."""
        # Define category-specific risk factors
        self.risk_factors = {
            "user": {
                "base_risk": RiskLevel.HIGH,
                "deletion_risk": RiskLevel.CRITICAL,
                "modification_risk": RiskLevel.HIGH,
                "creation_risk": RiskLevel.MEDIUM
            },
            "service": {
                "base_risk": RiskLevel.MEDIUM,
                "critical_services": {
                    "sshd", "systemd", "networkd", "dbus", "systemd-logind"
                }
            },
            "package": {
                "base_risk": RiskLevel.LOW,
                "system_packages": {
                    "systemd", "kernel", "glibc", "bash", "coreutils"
                }
            },
            "port": {
                "base_risk": RiskLevel.MEDIUM,
                "critical_ports": {22, 80, 443, 53}  # SSH, HTTP, HTTPS, DNS
            },
            "group": {
                "base_risk": RiskLevel.MEDIUM,
                "system_groups": {
                    "root", "sudo", "wheel", "admin", "docker"
                }
            },
            "cron": {
                "base_risk": RiskLevel.LOW,
                "system_cron_risk": RiskLevel.MEDIUM
            },
            "sysctl": {
                "base_risk": RiskLevel.MEDIUM,
                "security_params": {
                    "kernel.randomize_va_space",
                    "net.ipv4.ip_forward", 
                    "kernel.dmesg_restrict",
                    "kernel.kptr_restrict"
                }
            },
            "kernel_module": {
                "base_risk": RiskLevel.HIGH,
                "critical_modules": {
                    "ext4", "xfs", "btrfs", "network_drivers"
                }
            }
        }
    
    def enhance_change(self, base_change: BaseChange, context: Optional[Dict] = None) -> EnhancedChange:
        """
        Enhance a basic change with revert-specific analysis.
        
        Args:
            base_change: Basic change object from drift diff
            context: Optional context for enhanced analysis
            
        Returns:
            Enhanced change with risk assessment and feasibility analysis
        """
        enhanced = EnhancedChange.from_base_change(base_change)
        
        # Perform risk assessment
        enhanced.risk_level = self._assess_risk(enhanced)
        
        # Assess feasibility
        enhanced.feasibility, enhanced.feasibility_reason = self._assess_feasibility(enhanced)
        
        # Estimate duration
        enhanced.estimated_duration = self._estimate_duration(enhanced)
        
        # Assess rollback complexity
        enhanced.rollback_complexity = self._assess_rollback_complexity(enhanced)
        
        # Add operation-specific metadata
        enhanced.operation_metadata = self._generate_metadata(enhanced, context)
        
        return enhanced
    
    def enhance_changes(self, base_changes: List[BaseChange], context: Optional[Dict] = None) -> List[EnhancedChange]:
        """
        Enhance a list of basic changes.
        
        Args:
            base_changes: List of basic change objects
            context: Optional context for enhanced analysis
            
        Returns:
            List of enhanced changes with analysis
        """
        enhanced_changes = []
        
        for base_change in base_changes:
            enhanced = self.enhance_change(base_change, context)
            enhanced_changes.append(enhanced)
        
        # Detect dependencies between changes
        self._detect_change_dependencies(enhanced_changes)
        
        return enhanced_changes
    
    def _assess_risk(self, change: EnhancedChange) -> RiskLevel:
        """Assess risk level for a specific change."""
        category = change.category
        
        if category not in self.risk_factors:
            return RiskLevel.LOW
        
        factors = self.risk_factors[category]
        base_risk = factors.get("base_risk", RiskLevel.LOW)
        
        # Category-specific risk assessment
        if category == "user":
            if change.kind == "removed":
                return factors["deletion_risk"]
            elif change.kind == "modified":
                return factors["modification_risk"]
            elif change.kind == "added":
                return factors["creation_risk"]
        
        elif category == "service":
            critical_services = factors.get("critical_services", set())
            if change.name in critical_services:
                return RiskLevel.HIGH
            elif change.critical:
                return RiskLevel.HIGH
            else:
                return base_risk
        
        elif category == "package":
            system_packages = factors.get("system_packages", set())
            if any(pkg in change.name.lower() for pkg in system_packages):
                return RiskLevel.HIGH
            else:
                return base_risk
        
        elif category == "port":
            critical_ports = factors.get("critical_ports", set())
            try:
                port_num = int(change.name.split("/")[0])
                if port_num in critical_ports:
                    return RiskLevel.HIGH
            except (ValueError, IndexError):
                pass
            return base_risk
        
        elif category == "group":
            system_groups = factors.get("system_groups", set())
            if change.name in system_groups:
                return RiskLevel.HIGH
            else:
                return base_risk
        
        elif category == "sysctl":
            security_params = factors.get("security_params", set())
            if change.name in security_params:
                return RiskLevel.HIGH
            else:
                return base_risk
        
        elif category == "cron":
            if change.name.startswith("/etc/cron") or "root" in str(change.before or change.after):
                return factors["system_cron_risk"]
            else:
                return base_risk
        
        elif category == "kernel_module":
            return RiskLevel.HIGH  # All kernel module changes are high risk
        
        return base_risk
    
    def _assess_feasibility(self, change: EnhancedChange) -> tuple[OperationFeasibility, Optional[str]]:
        """Assess feasibility of reverting a specific change."""
        category = change.category
        kind = change.kind
        
        # Category-specific feasibility assessment
        if category == "package":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "service":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "user":
            if kind == "added":
                return OperationFeasibility.FEASIBLE, None
            elif kind == "removed":
                return OperationFeasibility.RISKY, "User recreation may not preserve all original attributes"
            elif kind == "modified":
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "group":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "cron":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "port":
            return OperationFeasibility.INFEASIBLE, "Ports are controlled by services, not directly manageable"
        
        elif category == "sysctl":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "mount":
            return OperationFeasibility.RISKY, "Mount operations require careful validation and may affect system stability"
        
        elif category == "env_var":
            if kind in ["added", "removed", "modified"]:
                return OperationFeasibility.FEASIBLE, None
        
        elif category == "kernel_module":
            if kind in ["added", "removed"]:
                return OperationFeasibility.RISKY, "Kernel module operations can affect system stability"
        
        # Default: assume risky for unknown categories
        return OperationFeasibility.RISKY, f"Unknown category '{category}' - manual review required"
    
    def _estimate_duration(self, change: EnhancedChange) -> int:
        """Estimate duration in seconds for reverting a change."""
        category = change.category
        
        # Category-specific duration estimates
        duration_map = {
            "package": 60,      # Package operations can be slow
            "service": 10,      # Service operations are usually quick
            "user": 5,          # User operations are quick
            "group": 5,         # Group operations are quick
            "cron": 2,          # Cron operations are very quick
            "port": 1,          # Port operations are instant (but infeasible)
            "sysctl": 2,        # Sysctl operations are quick
            "mount": 30,        # Mount operations can take time
            "env_var": 1,       # Environment variable operations are instant
            "kernel_module": 15 # Kernel module operations need time
        }
        
        base_duration = duration_map.get(category, 10)
        
        # Adjust based on risk level
        if change.risk_level == RiskLevel.CRITICAL:
            base_duration *= 2  # More time for critical operations
        elif change.risk_level == RiskLevel.HIGH:
            base_duration *= 1.5
        
        return int(base_duration)
    
    def _assess_rollback_complexity(self, change: EnhancedChange) -> RiskLevel:
        """Assess complexity of rolling back this change if it fails."""
        category = change.category
        
        # Some operations are harder to rollback than others
        if category in ["user", "kernel_module"]:
            return RiskLevel.HIGH
        elif category in ["service", "package", "mount"]:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _generate_metadata(self, change: EnhancedChange, context: Optional[Dict]) -> Dict[str, Any]:
        """Generate operation-specific metadata for the change."""
        metadata = {}
        
        # Add category-specific metadata
        if change.category == "package":
            metadata["package_manager"] = self._detect_package_manager(change)
        elif change.category == "service":
            metadata["service_manager"] = "systemd"  # Assume systemd for now
        elif change.category == "user":
            metadata["user_management"] = "system"
        
        # Add context information if available
        if context:
            metadata.update(context.get("metadata", {}))
        
        return metadata
    
    def _detect_package_manager(self, change: EnhancedChange) -> str:
        """Detect which package manager to use for a package change."""
        # This is a simplified detection - in reality, we'd need to check
        # the original package manager from the change metadata
        if hasattr(change.before, 'manager'):
            return change.before.manager
        elif hasattr(change.after, 'manager'):
            return change.after.manager
        else:
            return "unknown"
    
    def _detect_change_dependencies(self, changes: List[EnhancedChange]):
        """Detect dependencies between changes and update dependency lists."""
        # Create a mapping of changes by category
        by_category = {}
        for change in changes:
            if change.category not in by_category:
                by_category[change.category] = []
            by_category[change.category].append(change)
        
        # Define dependency rules
        dependency_rules = {
            "service": ["package", "user", "group"],
            "group": ["user"],
            "cron": ["user", "package"],
            "port": ["service"]
        }
        
        # Apply dependency rules
        for change in changes:
            if change.category in dependency_rules:
                prerequisites = dependency_rules[change.category]
                for prereq_category in prerequisites:
                    if prereq_category in by_category:
                        # Add dependencies to prerequisite changes
                        for prereq_change in by_category[prereq_category]:
                            if prereq_change.operation_id not in change.dependencies:
                                change.dependencies.append(prereq_change.operation_id)


def enhance_diff_changes(base_changes: List[BaseChange], context: Optional[Dict] = None) -> List[EnhancedChange]:
    """
    Convenience function to enhance a list of basic changes.
    
    Args:
        base_changes: List of basic Change objects from drift diff
        context: Optional context for enhanced analysis
        
    Returns:
        List of enhanced changes with revert-specific analysis
    """
    enhancer = ChangeEnhancer()
    return enhancer.enhance_changes(base_changes, context)