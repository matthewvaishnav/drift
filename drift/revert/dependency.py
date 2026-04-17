"""
drift/revert/dependency.py

Advanced dependency management for revert operations.
Handles dependency graph construction, topological sorting, circular dependency detection,
and conflict resolution between operations.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict, deque
from enum import Enum

from .models import Operation, RiskLevel

logger = logging.getLogger("drift.revert.dependency")


class DependencyType(Enum):
    """Types of dependencies between operations."""
    PREREQUISITE = "prerequisite"  # Must complete before this operation
    CONFLICT = "conflict"          # Cannot run at the same time
    ORDERING = "ordering"          # Preferred order but not strict requirement
    RESOURCE = "resource"          # Shares the same resource


class DependencyRelation:
    """Represents a dependency relationship between two operations."""
    
    def __init__(self, 
                 source_op_id: str, 
                 target_op_id: str, 
                 dependency_type: DependencyType,
                 reason: str = ""):
        self.source_op_id = source_op_id
        self.target_op_id = target_op_id
        self.dependency_type = dependency_type
        self.reason = reason
    
    def __repr__(self):
        return f"DependencyRelation({self.source_op_id} -> {self.target_op_id}, {self.dependency_type.value})"


class DependencyGraph:
    """
    Manages dependencies between operations.
    
    Provides functionality for building dependency graphs, detecting cycles,
    and performing topological sorting for execution order.
    """
    
    def __init__(self):
        """Initialize empty dependency graph."""
        self.operations: Dict[str, Operation] = {}
        self.dependencies: Dict[str, List[DependencyRelation]] = defaultdict(list)
        self.reverse_dependencies: Dict[str, List[DependencyRelation]] = defaultdict(list)
    
    def add_operation(self, operation: Operation):
        """Add an operation to the graph."""
        self.operations[operation.id] = operation
    
    def add_dependency(self, relation: DependencyRelation):
        """Add a dependency relationship."""
        self.dependencies[relation.source_op_id].append(relation)
        self.reverse_dependencies[relation.target_op_id].append(relation)
    
    def get_prerequisites(self, op_id: str) -> List[str]:
        """Get all operations that must complete before the given operation."""
        prerequisites = []
        for relation in self.reverse_dependencies[op_id]:
            if relation.dependency_type == DependencyType.PREREQUISITE:
                prerequisites.append(relation.source_op_id)
        return prerequisites
    
    def get_dependents(self, op_id: str) -> List[str]:
        """Get all operations that depend on the given operation."""
        dependents = []
        for relation in self.dependencies[op_id]:
            if relation.dependency_type == DependencyType.PREREQUISITE:
                dependents.append(relation.target_op_id)
        return dependents
    
    def has_cycle(self) -> Tuple[bool, Optional[List[str]]]:
        """
        Check for circular dependencies using DFS.
        
        Returns:
            Tuple of (has_cycle, cycle_path_if_found)
        """
        visited = set()
        rec_stack = set()
        parent = {}
        
        def dfs(node: str, path: List[str]) -> Optional[List[str]]:
            if node in rec_stack:
                # Found a cycle - return the cycle path
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]
            
            if node in visited:
                return None
            
            visited.add(node)
            rec_stack.add(node)
            
            # Follow prerequisite dependencies
            for relation in self.dependencies[node]:
                if relation.dependency_type == DependencyType.PREREQUISITE:
                    cycle = dfs(relation.target_op_id, path + [node])
                    if cycle:
                        return cycle
            
            rec_stack.remove(node)
            return None
        
        for op_id in self.operations:
            if op_id not in visited:
                cycle = dfs(op_id, [])
                if cycle:
                    return True, cycle
        
        return False, None
    
    def topological_sort(self) -> List[List[str]]:
        """
        Perform topological sort to get execution order.
        
        Returns:
            List of lists, where each inner list contains operations
            that can be executed in parallel (same level).
        """
        # Calculate in-degrees (number of prerequisites)
        in_degree = defaultdict(int)
        for op_id in self.operations:
            in_degree[op_id] = len(self.get_prerequisites(op_id))
        
        # Start with operations that have no prerequisites
        queue = deque([op_id for op_id in self.operations if in_degree[op_id] == 0])
        result = []
        
        while queue:
            # All operations in the current queue can be executed in parallel
            current_level = list(queue)
            result.append(current_level)
            queue.clear()
            
            # Process each operation in the current level
            for op_id in current_level:
                # Reduce in-degree for all dependent operations
                for dependent_id in self.get_dependents(op_id):
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        queue.append(dependent_id)
        
        # Check if all operations were processed (no cycles)
        total_processed = sum(len(level) for level in result)
        if total_processed != len(self.operations):
            logger.error(f"Topological sort incomplete: {total_processed}/{len(self.operations)} operations processed")
        
        return result
    
    def detect_conflicts(self) -> List[Tuple[str, str, str]]:
        """
        Detect conflicting operations.
        
        Returns:
            List of tuples (op1_id, op2_id, conflict_reason)
        """
        conflicts = []
        
        for op_id, relations in self.dependencies.items():
            for relation in relations:
                if relation.dependency_type == DependencyType.CONFLICT:
                    conflicts.append((op_id, relation.target_op_id, relation.reason))
        
        return conflicts
    
    def get_critical_path(self) -> List[str]:
        """
        Find the critical path (longest path) through the dependency graph.
        
        Returns:
            List of operation IDs representing the critical path
        """
        # This is a simplified version - in practice, you'd want to consider
        # operation durations for a true critical path analysis
        
        sorted_levels = self.topological_sort()
        if not sorted_levels:
            return []
        
        # For now, just return the path through the first operation in each level
        critical_path = []
        for level in sorted_levels:
            if level:
                # Choose the operation with highest risk or longest duration
                level_ops = [self.operations[op_id] for op_id in level]
                critical_op = max(level_ops, key=lambda op: (
                    op.risk_level.value == "critical",
                    op.risk_level.value == "high", 
                    op.estimated_duration
                ))
                critical_path.append(critical_op.id)
        
        return critical_path


class DependencyBuilder:
    """
    Builds dependency graphs from lists of operations.
    
    Analyzes operations to automatically detect dependencies based on
    categories, targets, and operation types.
    """
    
    def __init__(self):
        """Initialize the dependency builder."""
        self.category_dependencies = {
            # Services depend on packages and users
            "service": ["package", "user", "group"],
            # Groups depend on users
            "group": ["user"],
            # Cron jobs depend on users and packages
            "cron": ["user", "package"],
            # Ports are managed by services
            "port": ["service"],
        }
        
        self.conflicting_operations = {
            # Package operations on the same package conflict
            "package": ["install", "remove", "upgrade", "downgrade"],
            # Service operations on the same service may conflict
            "service": ["start", "stop", "restart"],
            # User operations on the same user conflict
            "user": ["create", "delete", "modify"],
        }
    
    def build_dependency_graph(self, operations: List[Operation]) -> DependencyGraph:
        """
        Build a complete dependency graph from a list of operations.
        
        Args:
            operations: List of operations to analyze
            
        Returns:
            Complete dependency graph with all relationships
        """
        graph = DependencyGraph()
        
        # Add all operations to the graph
        for op in operations:
            graph.add_operation(op)
        
        # Build category-based dependencies
        self._add_category_dependencies(graph, operations)
        
        # Build target-based dependencies
        self._add_target_dependencies(graph, operations)
        
        # Detect conflicts
        self._add_conflict_relationships(graph, operations)
        
        # Add ordering preferences
        self._add_ordering_preferences(graph, operations)
        
        logger.info(f"Built dependency graph with {len(operations)} operations")
        
        return graph
    
    def _add_category_dependencies(self, graph: DependencyGraph, operations: List[Operation]):
        """Add dependencies based on operation categories."""
        # Group operations by category
        by_category = defaultdict(list)
        for op in operations:
            by_category[op.category].append(op)
        
        # Add category-based dependencies
        for category, prereq_categories in self.category_dependencies.items():
            if category in by_category:
                dependent_ops = by_category[category]
                
                for prereq_category in prereq_categories:
                    if prereq_category in by_category:
                        prereq_ops = by_category[prereq_category]
                        
                        # Each operation in the dependent category depends on
                        # all operations in the prerequisite category
                        for dependent_op in dependent_ops:
                            for prereq_op in prereq_ops:
                                relation = DependencyRelation(
                                    source_op_id=prereq_op.id,
                                    target_op_id=dependent_op.id,
                                    dependency_type=DependencyType.PREREQUISITE,
                                    reason=f"{category} operations depend on {prereq_category} operations"
                                )
                                graph.add_dependency(relation)
    
    def _add_target_dependencies(self, graph: DependencyGraph, operations: List[Operation]):
        """Add dependencies based on operation targets (same resource)."""
        # Group operations by target
        by_target = defaultdict(list)
        for op in operations:
            key = f"{op.category}:{op.target}"
            by_target[key].append(op)
        
        # Operations on the same target may have dependencies
        for target_key, target_ops in by_target.items():
            if len(target_ops) > 1:
                # Sort by risk level and action type to determine order
                target_ops.sort(key=lambda op: (
                    op.action == "remove",  # Removals first
                    op.action == "stop",    # Stops before starts
                    op.risk_level.value == "low"  # High risk operations first
                ))
                
                # Create sequential dependencies
                for i in range(len(target_ops) - 1):
                    relation = DependencyRelation(
                        source_op_id=target_ops[i].id,
                        target_op_id=target_ops[i + 1].id,
                        dependency_type=DependencyType.PREREQUISITE,
                        reason=f"Sequential operations on {target_key}"
                    )
                    graph.add_dependency(relation)
    
    def _add_conflict_relationships(self, graph: DependencyGraph, operations: List[Operation]):
        """Add conflict relationships between incompatible operations."""
        # Group by category and target
        by_category_target = defaultdict(list)
        for op in operations:
            key = f"{op.category}:{op.target}"
            by_category_target[key].append(op)
        
        # Check for conflicting operations on the same target
        for target_key, target_ops in by_category_target.items():
            category = target_key.split(':')[0]
            
            if category in self.conflicting_operations:
                conflicting_actions = self.conflicting_operations[category]
                
                # Find operations with conflicting actions
                for i, op1 in enumerate(target_ops):
                    for op2 in target_ops[i + 1:]:
                        if (op1.action in conflicting_actions and 
                            op2.action in conflicting_actions and
                            op1.action != op2.action):
                            
                            relation = DependencyRelation(
                                source_op_id=op1.id,
                                target_op_id=op2.id,
                                dependency_type=DependencyType.CONFLICT,
                                reason=f"Conflicting {category} operations: {op1.action} vs {op2.action}"
                            )
                            graph.add_dependency(relation)
    
    def _add_ordering_preferences(self, graph: DependencyGraph, operations: List[Operation]):
        """Add ordering preferences for better execution flow."""
        # Group by risk level
        by_risk = defaultdict(list)
        for op in operations:
            by_risk[op.risk_level].append(op)
        
        # Prefer to do low-risk operations before high-risk ones when possible
        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        
        for i in range(len(risk_order) - 1):
            lower_risk_ops = by_risk[risk_order[i]]
            higher_risk_ops = by_risk[risk_order[i + 1]]
            
            # Add ordering preferences (not strict dependencies)
            for lower_op in lower_risk_ops:
                for higher_op in higher_risk_ops:
                    # Only add if there's no existing dependency relationship
                    existing_deps = graph.get_prerequisites(higher_op.id)
                    if lower_op.id not in existing_deps:
                        relation = DependencyRelation(
                            source_op_id=lower_op.id,
                            target_op_id=higher_op.id,
                            dependency_type=DependencyType.ORDERING,
                            reason=f"Prefer lower risk operations first"
                        )
                        graph.add_dependency(relation)


def validate_operation_plan_dependencies(operations: List[Operation]) -> Dict:
    """
    Validate dependencies in an operation plan.
    
    Args:
        operations: List of operations to validate
        
    Returns:
        Dictionary with validation results
    """
    builder = DependencyBuilder()
    graph = builder.build_dependency_graph(operations)
    
    # Check for cycles
    has_cycle, cycle_path = graph.has_cycle()
    
    # Detect conflicts
    conflicts = graph.detect_conflicts()
    
    # Get execution order
    execution_order = graph.topological_sort()
    
    # Calculate metrics
    total_levels = len(execution_order)
    max_parallel = max(len(level) for level in execution_order) if execution_order else 0
    critical_path = graph.get_critical_path()
    
    return {
        'valid': not has_cycle and len(conflicts) == 0,
        'has_cycles': has_cycle,
        'cycle_path': cycle_path,
        'conflicts': conflicts,
        'execution_levels': total_levels,
        'max_parallel_operations': max_parallel,
        'critical_path': critical_path,
        'total_operations': len(operations),
        'execution_order': execution_order
    }