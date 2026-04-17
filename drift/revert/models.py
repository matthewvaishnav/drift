"""
drift/revert/models.py

Data models for the auto-revert functionality.
These models define the structure for revert operations, options, results, and safety assessments.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union


class RiskLevel(Enum):
    """Risk levels for operations and safety assessments."""
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RevertOptions:
    """
    Configuration options for revert operations.
    
    Attributes:
        dry_run: If True, only plan operations without executing them
        force: If True, bypass safety validations and user confirmations
        skip_confirmation: If True, skip user confirmation prompts
        exclude_categories: Set of categories to exclude from revert (packages, services, etc.)
        timeout_seconds: Maximum time to wait for operations to complete
        create_backup: If True, create safety backup before revert
    """
    dry_run: bool = False
    force: bool = False
    skip_confirmation: bool = False
    exclude_categories: Set[str] = field(default_factory=set)
    timeout_seconds: int = 300
    create_backup: bool = True
    
    def __post_init__(self):
        """Validate options after initialization."""
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        
        # Validate exclude_categories contains only valid category names
        valid_categories = {
            "packages", "services", "ports", "users", "groups", 
            "cron", "sysctl", "mounts", "env_vars", "kernel_modules"
        }
        invalid_categories = self.exclude_categories - valid_categories
        if invalid_categories:
            raise ValueError(f"Invalid exclude_categories: {invalid_categories}")
        
        # Safety check: force and skip_confirmation cannot both be True in production
        if self.force and self.skip_confirmation and not self.dry_run:
            raise ValueError("force and skip_confirmation cannot both be True in production mode")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        result["exclude_categories"] = list(self.exclude_categories)
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RevertOptions":
        """Create from dictionary."""
        data = data.copy()
        if "exclude_categories" in data:
            data["exclude_categories"] = set(data["exclude_categories"])
        return cls(**data)


@dataclass
class Operation:
    """
    A single system operation to be executed during revert.
    
    Attributes:
        id: Unique identifier for this operation
        category: Category of operation (package, service, user, etc.)
        action: Type of action (install, remove, start, stop, create, delete)
        target: Name/identifier of the item being operated on
        command: Actual system command to execute
        risk_level: Risk level of this operation
        dependencies: List of operation IDs that must complete before this one
        rollback_command: Command to undo this operation if it fails
        estimated_duration: Estimated time in seconds for this operation
        description: Human-readable description of what this operation does
    """
    id: str
    category: str
    action: str
    target: str
    command: str
    risk_level: RiskLevel
    dependencies: List[str] = field(default_factory=list)
    rollback_command: Optional[str] = None
    estimated_duration: int = 30
    description: str = ""
    
    def __post_init__(self):
        """Validate operation after initialization."""
        if not self.id:
            raise ValueError("Operation ID must be non-empty")
        
        valid_categories = {
            "package", "service", "port", "user", "group", 
            "cron", "sysctl", "mount", "env_var", "kernel_module"
        }
        if self.category not in valid_categories:
            raise ValueError(f"Invalid category: {self.category}")
        
        if not self.command.strip():
            raise ValueError("Command must be non-empty")
        
        if self.estimated_duration < 0:
            raise ValueError("estimated_duration must be non-negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        result["risk_level"] = self.risk_level.value
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Operation":
        """Create from dictionary."""
        data = data.copy()
        if "risk_level" in data:
            data["risk_level"] = RiskLevel(data["risk_level"])
        return cls(**data)


@dataclass
class OperationBatch:
    """
    A batch of operations that can be executed in parallel.
    
    Attributes:
        id: Unique identifier for this batch
        operations: List of operations in this batch
        estimated_duration: Estimated time for the entire batch
    """
    id: str
    operations: List[Operation]
    estimated_duration: int = 0
    
    def __post_init__(self):
        """Calculate estimated duration from operations."""
        if self.operations:
            # For parallel operations, duration is the maximum of all operations
            self.estimated_duration = max(op.estimated_duration for op in self.operations)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "operations": [op.to_dict() for op in self.operations],
            "estimated_duration": self.estimated_duration
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationBatch":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            operations=[Operation.from_dict(op) for op in data["operations"]],
            estimated_duration=data.get("estimated_duration", 0)
        )


@dataclass
class OperationPlan:
    """
    Complete plan for executing a revert operation.
    
    Attributes:
        batches: List of operation batches to execute in sequence
        total_operations: Total number of individual operations
        estimated_duration: Estimated total time for all operations
        risk_assessment: Overall risk level for the entire plan
    """
    batches: List[OperationBatch]
    total_operations: int = 0
    estimated_duration: int = 0
    risk_assessment: RiskLevel = RiskLevel.LOW
    
    def __post_init__(self):
        """Calculate totals from batches."""
        self.total_operations = sum(len(batch.operations) for batch in self.batches)
        self.estimated_duration = sum(batch.estimated_duration for batch in self.batches)
        
        # Overall risk is the highest risk of any operation
        all_operations = [op for batch in self.batches for op in batch.operations]
        if all_operations:
            risk_levels = [op.risk_level for op in all_operations]
            # Find highest risk level
            risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
            self.risk_assessment = max(risk_levels, key=lambda r: risk_order.index(r))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "batches": [batch.to_dict() for batch in self.batches],
            "total_operations": self.total_operations,
            "estimated_duration": self.estimated_duration,
            "risk_assessment": self.risk_assessment.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationPlan":
        """Create from dictionary."""
        return cls(
            batches=[OperationBatch.from_dict(batch) for batch in data["batches"]],
            total_operations=data.get("total_operations", 0),
            estimated_duration=data.get("estimated_duration", 0),
            risk_assessment=RiskLevel(data.get("risk_assessment", "low"))
        )


@dataclass
class Risk:
    """
    Individual risk identified during safety assessment.
    
    Attributes:
        level: Risk level (low, medium, high, critical)
        message: Human-readable description of the risk
        operation: Optional operation that poses this risk
        mitigation: Optional suggestion for mitigating this risk
    """
    level: RiskLevel
    message: str
    operation: Optional[Operation] = None
    mitigation: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "level": self.level.value,
            "message": self.message,
            "operation": self.operation.to_dict() if self.operation else None,
            "mitigation": self.mitigation
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Risk":
        """Create from dictionary."""
        return cls(
            level=RiskLevel(data["level"]),
            message=data["message"],
            operation=Operation.from_dict(data["operation"]) if data.get("operation") else None,
            mitigation=data.get("mitigation")
        )


@dataclass
class SafetyAssessment:
    """
    Result of safety validation for a revert operation.
    
    Attributes:
        safe: Whether the operation is considered safe to proceed
        risks: List of identified risks
        requires_confirmation: Whether user confirmation is needed
        recommended_actions: List of recommended actions before proceeding
        backup_required: Whether a safety backup is required
        prerequisites_met: Whether all system prerequisites are satisfied
    """
    safe: bool
    risks: List[Risk] = field(default_factory=list)
    requires_confirmation: bool = False
    recommended_actions: List[str] = field(default_factory=list)
    backup_required: bool = True
    prerequisites_met: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "safe": self.safe,
            "risks": [risk.to_dict() for risk in self.risks],
            "requires_confirmation": self.requires_confirmation,
            "recommended_actions": self.recommended_actions,
            "backup_required": self.backup_required,
            "prerequisites_met": self.prerequisites_met
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SafetyAssessment":
        """Create from dictionary."""
        return cls(
            safe=data["safe"],
            risks=[Risk.from_dict(risk) for risk in data.get("risks", [])],
            requires_confirmation=data.get("requires_confirmation", False),
            recommended_actions=data.get("recommended_actions", []),
            backup_required=data.get("backup_required", True),
            prerequisites_met=data.get("prerequisites_met", True)
        )


@dataclass
class ExecutionResult:
    """
    Result of executing an operation or batch of operations.
    
    Attributes:
        success: Whether execution was successful
        operations_completed: Number of operations that completed successfully
        operations_failed: Number of operations that failed
        duration_seconds: Actual time taken for execution
        error_message: Error message if execution failed
        failed_operations: List of operations that failed
        rollback_performed: Whether rollback was performed for failed operations
    """
    success: bool
    operations_completed: int = 0
    operations_failed: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    failed_operations: List[Operation] = field(default_factory=list)
    rollback_performed: bool = False
    
    def __post_init__(self):
        """Validate execution result."""
        if self.operations_failed > 0 and self.success:
            raise ValueError("Cannot have failed operations with success=True")
        
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "operations_completed": self.operations_completed,
            "operations_failed": self.operations_failed,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "failed_operations": [op.to_dict() for op in self.failed_operations],
            "rollback_performed": self.rollback_performed
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionResult":
        """Create from dictionary."""
        return cls(
            success=data["success"],
            operations_completed=data.get("operations_completed", 0),
            operations_failed=data.get("operations_failed", 0),
            duration_seconds=data.get("duration_seconds", 0.0),
            error_message=data.get("error_message"),
            failed_operations=[Operation.from_dict(op) for op in data.get("failed_operations", [])],
            rollback_performed=data.get("rollback_performed", False)
        )


@dataclass
class RevertResult:
    """
    Complete result of a revert operation.
    
    Attributes:
        success: Whether the revert was successful
        revert_id: Unique identifier for this revert operation
        target_hash: Hash of the target snapshot
        backup_hash: Hash of safety backup snapshot (if created)
        operations_executed: Number of operations that were executed
        operations_failed: Number of operations that failed
        duration_seconds: Total time taken for the revert
        error_message: Error message if revert failed
        failed_operations: List of operations that failed
        operation_plan: The operation plan that was executed (for dry runs)
        safety_assessment: Safety assessment that was performed
    """
    success: bool
    revert_id: str
    target_hash: str
    backup_hash: Optional[str] = None
    operations_executed: int = 0
    operations_failed: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    failed_operations: List[Operation] = field(default_factory=list)
    operation_plan: Optional[OperationPlan] = None
    safety_assessment: Optional[SafetyAssessment] = None
    
    def __post_init__(self):
        """Validate revert result."""
        if self.operations_executed + self.operations_failed < 0:
            raise ValueError("Operation counts must be non-negative")
        
        if self.operations_failed > 0 and self.success:
            raise ValueError("Cannot have failed operations with success=True")
        
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "revert_id": self.revert_id,
            "target_hash": self.target_hash,
            "backup_hash": self.backup_hash,
            "operations_executed": self.operations_executed,
            "operations_failed": self.operations_failed,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "failed_operations": [op.to_dict() for op in self.failed_operations],
            "operation_plan": self.operation_plan.to_dict() if self.operation_plan else None,
            "safety_assessment": self.safety_assessment.to_dict() if self.safety_assessment else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RevertResult":
        """Create from dictionary."""
        return cls(
            success=data["success"],
            revert_id=data["revert_id"],
            target_hash=data["target_hash"],
            backup_hash=data.get("backup_hash"),
            operations_executed=data.get("operations_executed", 0),
            operations_failed=data.get("operations_failed", 0),
            duration_seconds=data.get("duration_seconds", 0.0),
            error_message=data.get("error_message"),
            failed_operations=[Operation.from_dict(op) for op in data.get("failed_operations", [])],
            operation_plan=OperationPlan.from_dict(data["operation_plan"]) if data.get("operation_plan") else None,
            safety_assessment=SafetyAssessment.from_dict(data["safety_assessment"]) if data.get("safety_assessment") else None
        )


# Utility functions for working with models

def generate_operation_id(category: str, action: str, target: str) -> str:
    """Generate a unique operation ID from category, action, and target."""
    import hashlib
    content = f"{category}:{action}:{target}"
    return hashlib.md5(content.encode()).hexdigest()[:8]


def generate_revert_id() -> str:
    """Generate a unique revert operation ID."""
    import uuid
    return str(uuid.uuid4())[:8]


def serialize_to_json(obj: Union[RevertOptions, Operation, OperationPlan, SafetyAssessment, RevertResult]) -> str:
    """Serialize any revert model to JSON string."""
    return json.dumps(obj.to_dict(), indent=2)


def deserialize_from_json(json_str: str, model_class) -> Union[RevertOptions, Operation, OperationPlan, SafetyAssessment, RevertResult]:
    """Deserialize JSON string to specified model class."""
    data = json.loads(json_str)
    return model_class.from_dict(data)