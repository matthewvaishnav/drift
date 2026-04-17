"""
drift/revert/analyzer.py

Revert-specific diff analyzer that extends the existing drift diff functionality
to provide analysis tailored for revert operations, including risk assessment,
dependency detection, and operation feasibility validation.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Set, Tuple

from drift.models import Snapshot, Change, DiffResult
from drift.diff import diff_snapshots

from .models import RiskLevel, Operation, generate_operation_id

logger = logging.getLogger("drift.revert.analyzer")


class RevertDiff:
    """
    Enhanced diff result specifically for revert operations.
    
    Extends the basic DiffResult with revert-specific analysis including
    risk assessment, dependency tracking, and operation feasibility.
    """
    
    def __init__(self, diff_result: DiffResult):
        """Initialize from a standard DiffResult."""
        self.diff_result = diff_result
        self.changes = diff_result.changes
        self.before_hash = diff_result.before_hash
        self.after_hash = diff_result.after_hash
        self.before_time = diff_result.before_time
        self.after_time = diff_result.after_time
        self.hostname = diff_result.hostname
        
        # Revert-specific analysis
        self.categorized_changes: Dict[str, List[Change]] = {}
        self.risk_assessments: Dict[str, RiskLevel] = {}
        self.dependencies: Dict[str, List[str]] = {}
        self.complexity_score: int = 0
        self.feasible_operations: Set[str] = set()
        self.infeasible_operations: Dict[str, str] = {}  # operation_id -> reason
        
        # Perform analysis
        self._analyze_changes()
    
    def _analyze_changes(self):
        """Perform comprehensive analysis of changes for revert operations."""
        self.categorized_changes = self.diff_result.by_category
        self._assess_risks()
        self._detect_dependencies()
        self._calculate_complexity()
        self._validate_feasibility()
    
    def _assess_risks(self):
        """Assess risk levels for each category of changes."""
        for category, changes in self.categorized_changes.items():
            if not changes:
                continue
                
            # Determine risk level based on category and change types
            risk_level = self._calculate_category_risk(category, changes)
            self.risk_assessments[category] = risk_level
    
    def _calculate_category_risk(self, category: str, changes: List[Change]) -> RiskLevel:
        """Calculate risk level for a specific category."""
        # Count different types of changes
        added = sum(1 for c in changes if c.kind == "added")
        removed = sum(1 for c in changes if c.kind == "removed")
        modified = sum(1 for c in changes if c.kind == "modified")
        critical = sum(1 for c in changes if c.critical)
        
        # Category-specific risk assessment
        if category == "user":
            # User operations are inherently risky
            if removed > 0:
                return RiskLevel.CRITICAL  # Deleting users is very dangerous
            elif added > 0 or modified > 0:
                return RiskLevel.HIGH
            else:
                return RiskLevel.LOW
                
        elif category == "service":
            # Service operations can affect system availability
            if critical > 0:
                return RiskLevel.HIGH
            elif removed > 0 or (added + modified) > 3:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
                
        elif category == "package":
            # Package operations can be slow and have dependencies
            total_changes = added + removed + modified
            if total_changes > 20:
                return RiskLevel.HIGH
            elif total_changes > 5:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
                
        elif category == "port":
            # Port changes can affect network security
            if added > 0:  # New listening ports are security-relevant
                return RiskLevel.HIGH
            elif removed > 0 or modified > 0:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
                
        elif category == "group":
            # Group changes can affect permissions
            if removed > 0 or modified > 0:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
                
        elif category in ["cron", "sysctl", "kernel_module"]:
            # System-level changes are potentially risky
            if critical > 0:
                return RiskLevel.HIGH
            elif (added + removed + modified) > 0:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
                
        else:
            # Default risk assessment for other categories
            if critical > 0:
                return RiskLevel.HIGH
            elif (added + removed + modified) > 2:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
    
    def _detect_dependencies(self):
        """Detect dependencies between different categories of changes."""
        categories = list(self.categorized_changes.keys())
        
        # Define dependency relationships
        # Format: dependent_category -> [prerequisite_categories]
        dependency_rules = {
            "service": ["package", "user", "group"],  # Services may depend on packages and users
            "group": ["user"],  # Groups may depend on users existing
            "cron": ["user", "package"],  # Cron jobs may depend on users and packages
            "port": ["service", "package"],  # Ports typically opened by services/packages
        }
        
        for category in categories:
            if category in dependency_rules:
                prerequisites = []
                for prereq_category in dependency_rules[category]:
                    if prereq_category in categories:
                        prerequisites.append(prereq_category)
                
                if prerequisites:
                    self.dependencies[category] = prerequisites
    
    def _calculate_complexity(self):
        """Calculate overall complexity score for the revert operation."""
        complexity = 0
        
        for category, changes in self.categorized_changes.items():
            if not changes:
                continue
                
            # Base complexity from number of changes
            complexity += len(changes)
            
            # Category-specific complexity multipliers
            multipliers = {
                "package": 3,  # Package operations are slow
                "service": 2,  # Service operations need careful sequencing
                "user": 4,     # User operations are complex and risky
                "group": 2,    # Group operations affect permissions
                "cron": 1,     # Cron operations are relatively simple
                "port": 1,     # Port operations are usually automatic
                "sysctl": 2,   # System parameters need validation
                "mount": 3,    # Mount operations can be complex
                "env_var": 1,  # Environment variables are simple
                "kernel_module": 4,  # Kernel modules are complex and risky
            }
            
            multiplier = multipliers.get(category, 1)
            complexity += len(changes) * multiplier
            
            # Add complexity for critical changes
            critical_changes = sum(1 for c in changes if c.critical)
            complexity += critical_changes * 5
        
        # Add complexity for dependencies
        complexity += len(self.dependencies) * 2
        
        self.complexity_score = complexity
    
    def _validate_feasibility(self):
        """Validate which operations are feasible to revert."""
        for category, changes in self.categorized_changes.items():
            for change in changes:
                operation_id = generate_operation_id(category, change.kind, change.name)
                
                # Check if this type of operation is feasible
                feasible, reason = self._is_operation_feasible(category, change)
                
                if feasible:
                    self.feasible_operations.add(operation_id)
                else:
                    self.infeasible_operations[operation_id] = reason
    
    def _is_operation_feasible(self, category: str, change: Change) -> Tuple[bool, Optional[str]]:
        """Check if a specific change can be feasibly reverted."""
        
        # Category-specific feasibility checks
        if category == "package":
            if change.kind == "removed":
                # Can reinstall removed packages
                return True, None
            elif change.kind == "added":
                # Can remove added packages (if no dependencies)
                return True, None
            elif change.kind == "modified":
                # Can change package versions (with some risk)
                return True, None
                
        elif category == "service":
            if change.kind in ["added", "removed", "modified"]:
                # Service state changes are generally feasible
                return True, None
                
        elif category == "user":
            if change.kind == "added":
                # Can remove added users
                return True, None
            elif change.kind == "removed":
                # Cannot easily recreate removed users with same state
                return False, "User recreation may not preserve all attributes"
            elif change.kind == "modified":
                # Can modify user attributes
                return True, None
                
        elif category == "group":
            if change.kind in ["added", "removed", "modified"]:
                # Group operations are generally feasible
                return True, None
                
        elif category == "cron":
            if change.kind in ["added", "removed", "modified"]:
                # Cron operations are feasible
                return True, None
                
        elif category == "port":
            if change.kind == "added":
                # Cannot directly close ports - they're opened by services
                return False, "Ports are controlled by services, not directly manageable"
            elif change.kind == "removed":
                # Cannot directly open ports
                return False, "Ports are controlled by services, not directly manageable"
            else:
                return False, "Port changes are not directly revertible"
                
        elif category == "sysctl":
            if change.kind in ["added", "removed", "modified"]:
                # Sysctl parameters can be changed
                return True, None
                
        elif category == "mount":
            if change.kind in ["added", "removed"]:
                # Mount operations are complex and risky
                return False, "Mount operations require manual intervention"
            elif change.kind == "modified":
                return False, "Mount modifications require manual intervention"
                
        elif category == "env_var":
            if change.kind in ["added", "removed", "modified"]:
                # Environment variable changes are feasible
                return True, None
                
        elif category == "kernel_module":
            if change.kind == "added":
                # Can unload added modules
                return True, None
            elif change.kind == "removed":
                # Can reload removed modules
                return True, None
            else:
                return True, None
        
        # Default: assume feasible but flag for review
        return True, None
    
    def get_high_risk_changes(self) -> List[Change]:
        """Get all changes that are considered high risk."""
        high_risk_changes = []
        
        for category, risk_level in self.risk_assessments.items():
            if risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                high_risk_changes.extend(self.categorized_changes.get(category, []))
        
        return high_risk_changes
    
    def get_infeasible_changes(self) -> List[Tuple[Change, str]]:
        """Get all changes that cannot be feasibly reverted."""
        infeasible_changes = []
        
        for category, changes in self.categorized_changes.items():
            for change in changes:
                operation_id = generate_operation_id(category, change.kind, change.name)
                if operation_id in self.infeasible_operations:
                    reason = self.infeasible_operations[operation_id]
                    infeasible_changes.append((change, reason))
        
        return infeasible_changes
    
    def get_dependency_order(self) -> List[str]:
        """Get categories in dependency order (prerequisites first)."""
        # Topological sort of dependencies
        visited = set()
        temp_visited = set()
        result = []
        
        def visit(category: str):
            if category in temp_visited:
                # Circular dependency - use default order
                return
            if category in visited:
                return
                
            temp_visited.add(category)
            
            # Visit prerequisites first
            for prereq in self.dependencies.get(category, []):
                visit(prereq)
            
            temp_visited.remove(category)
            visited.add(category)
            result.append(category)
        
        # Visit all categories
        for category in self.categorized_changes.keys():
            if category not in visited:
                visit(category)
        
        return result


class DiffAnalyzer:
    """
    Revert-specific diff analyzer that extends existing diff functionality.
    
    Provides enhanced analysis for revert operations including risk assessment,
    dependency detection, and operation feasibility validation.
    """
    
    def __init__(self):
        """Initialize the diff analyzer."""
        self.logger = logging.getLogger("drift.revert.analyzer.DiffAnalyzer")
    
    def analyze_revert_diff(self, current: Snapshot, target: Snapshot) -> RevertDiff:
        """
        Analyze differences between current and target snapshots for revert operations.
        
        Args:
            current: Current system state snapshot
            target: Target snapshot to revert to
            
        Returns:
            RevertDiff with comprehensive revert-specific analysis
        """
        self.logger.info(f"Analyzing revert diff: {current.digest()[:8]} -> {target.digest()[:8]}")
        
        # Use existing diff engine to get basic differences
        diff_result = diff_snapshots(current, target)
        
        # Create enhanced revert diff with additional analysis
        revert_diff = RevertDiff(diff_result)
        
        self.logger.info(f"Analysis complete: {len(revert_diff.changes)} changes, "
                        f"complexity score: {revert_diff.complexity_score}")
        
        return revert_diff
    
    def categorize_changes(self, diff_result: DiffResult) -> Dict[str, List[Change]]:
        """
        Categorize changes by operation type and risk level.
        
        Args:
            diff_result: Basic diff result from drift diff engine
            
        Returns:
            Dictionary mapping categories to lists of changes
        """
        return diff_result.by_category
    
    def estimate_complexity(self, changes: Dict[str, List[Change]]) -> int:
        """
        Estimate complexity score for a set of categorized changes.
        
        Args:
            changes: Dictionary of categorized changes
            
        Returns:
            Complexity score (higher = more complex)
        """
        # Create a temporary RevertDiff to calculate complexity
        # This is a bit of a hack, but reuses the existing logic
        from drift.models import Snapshot
        
        # Create dummy snapshots for complexity calculation
        dummy_current = Snapshot.new()
        dummy_target = Snapshot.new()
        
        # Create dummy diff result
        dummy_diff = DiffResult(
            before_hash="dummy1",
            after_hash="dummy2", 
            before_time="2024-01-01T00:00:00Z",
            after_time="2024-01-01T00:00:00Z",
            hostname="dummy",
            changes=[]
        )
        
        # Flatten changes for dummy diff
        for category_changes in changes.values():
            dummy_diff.changes.extend(category_changes)
        
        revert_diff = RevertDiff(dummy_diff)
        return revert_diff.complexity_score
    
    def assess_category_risk(self, category: str, changes: List[Change]) -> RiskLevel:
        """
        Assess risk level for a specific category of changes.
        
        Args:
            category: Category name (package, service, user, etc.)
            changes: List of changes in this category
            
        Returns:
            Risk level for this category
        """
        # Create temporary RevertDiff to use risk assessment logic
        from drift.models import Snapshot
        
        dummy_diff = DiffResult(
            before_hash="dummy1",
            after_hash="dummy2",
            before_time="2024-01-01T00:00:00Z", 
            after_time="2024-01-01T00:00:00Z",
            hostname="dummy",
            changes=changes
        )
        
        revert_diff = RevertDiff(dummy_diff)
        return revert_diff._calculate_category_risk(category, changes)
    
    def detect_dependencies(self, categorized_changes: Dict[str, List[Change]]) -> Dict[str, List[str]]:
        """
        Detect dependencies between different categories of changes.
        
        Args:
            categorized_changes: Dictionary of categorized changes
            
        Returns:
            Dictionary mapping categories to their prerequisites
        """
        # Create temporary RevertDiff to use dependency detection logic
        from drift.models import Snapshot
        
        dummy_diff = DiffResult(
            before_hash="dummy1",
            after_hash="dummy2",
            before_time="2024-01-01T00:00:00Z",
            after_time="2024-01-01T00:00:00Z", 
            hostname="dummy",
            changes=[]
        )
        
        revert_diff = RevertDiff(dummy_diff)
        revert_diff.categorized_changes = categorized_changes
        revert_diff._detect_dependencies()
        
        return revert_diff.dependencies
    
    def validate_operation_feasibility(self, category: str, change: Change) -> Tuple[bool, Optional[str]]:
        """
        Validate whether a specific change can be feasibly reverted.
        
        Args:
            category: Category of the change
            change: The change to validate
            
        Returns:
            Tuple of (feasible, reason_if_not_feasible)
        """
        # Create temporary RevertDiff to use feasibility validation logic
        dummy_diff = DiffResult(
            before_hash="dummy1",
            after_hash="dummy2",
            before_time="2024-01-01T00:00:00Z",
            after_time="2024-01-01T00:00:00Z",
            hostname="dummy", 
            changes=[change]
        )
        
        revert_diff = RevertDiff(dummy_diff)
        return revert_diff._is_operation_feasible(category, change)