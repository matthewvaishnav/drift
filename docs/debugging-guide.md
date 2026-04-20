# Drift Revert Debugging Guide

This guide provides comprehensive debugging techniques and tools for developing and troubleshooting the drift revert system.

## Table of Contents

- [Development Environment Setup](#development-environment-setup)
- [Logging and Diagnostics](#logging-and-diagnostics)
- [Common Issues and Solutions](#common-issues-and-solutions)
- [Debugging Tools](#debugging-tools)
- [Testing and Validation](#testing-and-validation)
- [Performance Debugging](#performance-debugging)
- [Error Analysis](#error-analysis)
- [Development Workflows](#development-workflows)

## Development Environment Setup

### Debug Configuration

```python
# ~/.drift/config.json
{
    "debug": {
        "enabled": true,
        "log_level": "DEBUG",
        "log_file": "/tmp/drift-debug.log",
        "trace_operations": true,
        "save_intermediate_state": true,
        "mock_execution": false
    },
    "revert": {
        "dry_run_by_default": true,
        "safety_checks_enabled": true,
        "detailed_logging": true
    }
}
```

### Environment Variables

```bash
# Enable debug mode
export DRIFT_DEBUG=1
export DRIFT_LOG_LEVEL=DEBUG
export DRIFT_LOG_FILE=/tmp/drift-debug.log

# Enable operation tracing
export DRIFT_TRACE_OPERATIONS=1

# Mock execution for testing
export DRIFT_MOCK_EXECUTION=1

# Preserve intermediate files
export DRIFT_PRESERVE_TEMP=1
```

### Development Installation

```bash
# Install in development mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"

# Install debugging tools
pip install pdb-attach ipdb memory-profiler line-profiler
```

## Logging and Diagnostics

### Structured Logging Setup

```python
# drift/revert/logging.py
import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any

class RevertLogger:
    """Structured logger for revert operations."""
    
    def __init__(self, name: str = "drift.revert"):
        self.logger = logging.getLogger(name)
        self.setup_logging()
    
    def setup_logging(self):
        """Configure structured logging."""
        handler = logging.StreamHandler(sys.stdout)
        formatter = StructuredFormatter()
        handler.setFormatter(formatter)
        
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG if os.getenv('DRIFT_DEBUG') else logging.INFO)
    
    def log_operation(self, operation_id: str, phase: str, data: Dict[str, Any]):
        """Log operation with structured data."""
        self.logger.info("operation", extra={
            "operation_id": operation_id,
            "phase": phase,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        })
    
    def log_error(self, error: Exception, context: Dict[str, Any]):
        """Log error with full context."""
        self.logger.error("error", extra={
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
            "timestamp": datetime.utcnow().isoformat()
        })

class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logs."""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }
        
        # Add extra fields
        if hasattr(record, 'operation_id'):
            log_data.update(record.__dict__)
        
        return json.dumps(log_data)
```

### Debug Decorators

```python
# drift/revert/debug.py
import functools
import time
import traceback
from typing import Any, Callable

def debug_trace(func: Callable) -> Callable:
    """Decorator to trace function calls."""
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = RevertLogger()
        
        # Log function entry
        logger.logger.debug(f"ENTER {func.__name__}", extra={
            "function": func.__name__,
            "args": str(args)[:200],  # Truncate long args
            "kwargs": str(kwargs)[:200]
        })
        
        start_time = time.time()
        
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            
            # Log successful exit
            logger.logger.debug(f"EXIT {func.__name__}", extra={
                "function": func.__name__,
                "duration": duration,
                "success": True
            })
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            
            # Log error exit
            logger.logger.error(f"ERROR {func.__name__}", extra={
                "function": func.__name__,
                "duration": duration,
                "error": str(e),
                "traceback": traceback.format_exc()
            })
            
            raise
    
    return wrapper

def performance_monitor(func: Callable) -> Callable:
    """Decorator to monitor performance."""
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        
        # Capture initial state
        start_memory = process.memory_info().rss
        start_cpu = process.cpu_percent()
        start_time = time.time()
        
        try:
            result = func(*args, **kwargs)
            
            # Capture final state
            end_memory = process.memory_info().rss
            end_cpu = process.cpu_percent()
            duration = time.time() - start_time
            
            # Log performance metrics
            logger = RevertLogger()
            logger.logger.info("performance", extra={
                "function": func.__name__,
                "duration": duration,
                "memory_delta": end_memory - start_memory,
                "cpu_percent": end_cpu,
                "peak_memory": end_memory
            })
            
            return result
            
        except Exception:
            raise
    
    return wrapper
```

## Common Issues and Solutions

### Issue 1: Operation Planning Failures

**Symptoms:**
- Empty operation plans
- Missing dependencies
- Incorrect operation sequencing

**Debugging Steps:**

```python
# Enable detailed planning logs
def debug_operation_planning():
    from drift.revert.planner import OperationPlanner
    
    planner = OperationPlanner()
    
    # Add debug logging to planner
    original_plan = planner.plan_operations
    
    def debug_plan_operations(revert_diff, options):
        logger = RevertLogger()
        
        logger.log_operation("planning", "start", {
            "changes_count": len(revert_diff.changes),
            "complexity_score": revert_diff.complexity_score
        })
        
        # Log each change
        for i, change in enumerate(revert_diff.changes):
            logger.log_operation("planning", "change", {
                "index": i,
                "category": change.change.category,
                "action": change.revert_action,
                "target": change.change.item
            })
        
        result = original_plan(revert_diff, options)
        
        logger.log_operation("planning", "complete", {
            "batches_count": len(result.batches),
            "total_operations": result.total_operations
        })
        
        return result
    
    planner.plan_operations = debug_plan_operations
    return planner
```

**Common Fixes:**
- Check change detection logic
- Verify dependency calculation
- Validate operation generation

### Issue 2: Safety Validation Errors

**Symptoms:**
- Unexpected safety failures
- Incorrect risk assessments
- Missing prerequisites

**Debugging Steps:**

```python
def debug_safety_validation():
    from drift.revert.safety import SafetyValidator
    
    validator = SafetyValidator()
    
    # Override assess_safety with debugging
    original_assess = validator.assess_safety
    
    def debug_assess_safety(operation_plan, options):
        logger = RevertLogger()
        
        # Log each validation step
        logger.log_operation("safety", "start", {
            "operations_count": operation_plan.total_operations,
            "force_enabled": options.force
        })
        
        # Check each operation individually
        for batch in operation_plan.batches:
            for op in batch.operations:
                risk = validator.risk_assessor.assess_risk(op)
                logger.log_operation("safety", "operation_risk", {
                    "operation_id": op.id,
                    "category": op.category,
                    "action": op.action,
                    "risk_level": risk.value
                })
        
        result = original_assess(operation_plan, options)
        
        logger.log_operation("safety", "complete", {
            "safe": result.safe,
            "risks_count": len(result.risks),
            "requires_confirmation": result.requires_confirmation
        })
        
        return result
    
    validator.assess_safety = debug_assess_safety
    return validator
```

### Issue 3: Command Execution Failures

**Symptoms:**
- Commands fail unexpectedly
- Rollback issues
- Permission errors

**Debugging Steps:**

```python
def debug_command_execution():
    from drift.revert.executor import CommandExecutor
    
    executor = CommandExecutor()
    
    # Override execute_command with debugging
    original_execute = executor.execute_command
    
    def debug_execute_command(command, timeout=30):
        logger = RevertLogger()
        
        logger.log_operation("execution", "command_start", {
            "command": command,
            "timeout": timeout
        })
        
        try:
            result = original_execute(command, timeout)
            
            logger.log_operation("execution", "command_success", {
                "command": command,
                "return_code": result.returncode,
                "stdout": result.stdout[:500],  # Truncate
                "stderr": result.stderr[:500]
            })
            
            return result
            
        except Exception as e:
            logger.log_operation("execution", "command_error", {
                "command": command,
                "error": str(e)
            })
            raise
    
    executor.execute_command = debug_execute_command
    return executor
```

## Debugging Tools

### Interactive Debugger Integration

```python
# drift/revert/debug_tools.py
import pdb
import sys
from typing import Any

class RevertDebugger:
    """Interactive debugger for revert operations."""
    
    def __init__(self):
        self.breakpoints = set()
        self.enabled = os.getenv('DRIFT_DEBUG_INTERACTIVE', '0') == '1'
    
    def set_breakpoint(self, name: str):
        """Set a named breakpoint."""
        if self.enabled:
            self.breakpoints.add(name)
    
    def breakpoint(self, name: str, locals_dict: dict = None):
        """Conditional breakpoint."""
        if name in self.breakpoints:
            print(f"\n=== DRIFT DEBUG BREAKPOINT: {name} ===")
            if locals_dict:
                print("Local variables:")
                for key, value in locals_dict.items():
                    print(f"  {key}: {repr(value)[:100]}")
            print("=== Enter 'c' to continue ===")
            pdb.set_trace()

# Usage in code:
debugger = RevertDebugger()

def some_revert_function():
    # Set breakpoint
    debugger.breakpoint("before_operation_planning", locals())
    
    # ... rest of function
```

### State Inspection Tools

```python
class StateInspector:
    """Tool for inspecting revert system state."""
    
    def __init__(self):
        self.snapshots = []
    
    def capture_state(self, name: str, data: Any):
        """Capture system state at a point in time."""
        self.snapshots.append({
            "name": name,
            "timestamp": datetime.utcnow(),
            "data": self._serialize_data(data)
        })
    
    def _serialize_data(self, data: Any) -> dict:
        """Serialize data for inspection."""
        if hasattr(data, 'to_dict'):
            return data.to_dict()
        elif isinstance(data, (list, tuple)):
            return [self._serialize_data(item) for item in data]
        elif isinstance(data, dict):
            return {k: self._serialize_data(v) for k, v in data.items()}
        else:
            return str(data)
    
    def save_snapshots(self, filename: str):
        """Save captured states to file."""
        with open(filename, 'w') as f:
            json.dump(self.snapshots, f, indent=2, default=str)
    
    def compare_states(self, name1: str, name2: str):
        """Compare two captured states."""
        state1 = next((s for s in self.snapshots if s["name"] == name1), None)
        state2 = next((s for s in self.snapshots if s["name"] == name2), None)
        
        if not state1 or not state2:
            print("One or both states not found")
            return
        
        # Simple diff
        import difflib
        
        lines1 = json.dumps(state1["data"], indent=2).splitlines()
        lines2 = json.dumps(state2["data"], indent=2).splitlines()
        
        diff = difflib.unified_diff(lines1, lines2, 
                                  fromfile=name1, tofile=name2)
        
        for line in diff:
            print(line)
```

### Mock Execution Environment

```python
class MockExecutor:
    """Mock executor for testing without system changes."""
    
    def __init__(self):
        self.executed_commands = []
        self.command_results = {}
        self.failure_patterns = []
    
    def set_command_result(self, command_pattern: str, result: dict):
        """Set expected result for command pattern."""
        self.command_results[command_pattern] = result
    
    def add_failure_pattern(self, pattern: str):
        """Add command pattern that should fail."""
        self.failure_patterns.append(pattern)
    
    def execute_command(self, command: str, timeout: int = 30):
        """Mock command execution."""
        self.executed_commands.append({
            "command": command,
            "timestamp": datetime.utcnow(),
            "timeout": timeout
        })
        
        # Check for failure patterns
        for pattern in self.failure_patterns:
            if pattern in command:
                raise subprocess.CalledProcessError(1, command, "Mock failure")
        
        # Check for predefined results
        for pattern, result in self.command_results.items():
            if pattern in command:
                return MockResult(**result)
        
        # Default success
        return MockResult(returncode=0, stdout="Mock success", stderr="")

class MockResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
```

## Testing and Validation

### Unit Test Debugging

```python
# tests/debug_helpers.py
import pytest
from drift.revert.debug_tools import StateInspector

@pytest.fixture
def state_inspector():
    """Provide state inspector for tests."""
    return StateInspector()

@pytest.fixture
def debug_revert_engine():
    """Provide debug-enabled revert engine."""
    from drift.revert import RevertEngine
    
    engine = RevertEngine()
    
    # Add debugging hooks
    original_initiate = engine.initiate_revert
    
    def debug_initiate_revert(target_hash, options):
        print(f"DEBUG: Initiating revert to {target_hash}")
        print(f"DEBUG: Options: {options}")
        
        result = original_initiate(target_hash, options)
        
        print(f"DEBUG: Result success: {result.success}")
        if not result.success:
            print(f"DEBUG: Error: {result.error_message}")
        
        return result
    
    engine.initiate_revert = debug_initiate_revert
    return engine

# Usage in tests:
def test_revert_with_debugging(debug_revert_engine, state_inspector):
    state_inspector.capture_state("test_start", {"target": "abc123"})
    
    result = debug_revert_engine.initiate_revert("abc123", RevertOptions())
    
    state_inspector.capture_state("test_end", result)
    state_inspector.save_snapshots("/tmp/test_debug.json")
    
    assert result.success
```

### Integration Test Debugging

```python
def debug_integration_test():
    """Run integration test with full debugging."""
    
    # Enable all debug options
    os.environ.update({
        'DRIFT_DEBUG': '1',
        'DRIFT_LOG_LEVEL': 'DEBUG',
        'DRIFT_TRACE_OPERATIONS': '1',
        'DRIFT_MOCK_EXECUTION': '1'
    })
    
    from drift.revert import RevertEngine
    from tests.fixtures import create_test_snapshots
    
    # Create test scenario
    current, target = create_test_snapshots()
    
    # Add debugging
    inspector = StateInspector()
    inspector.capture_state("snapshots_created", {
        "current": current.hash,
        "target": target.hash
    })
    
    # Run revert
    engine = RevertEngine()
    result = engine.initiate_revert(target.hash, RevertOptions(dry_run=True))
    
    inspector.capture_state("revert_complete", result)
    inspector.save_snapshots("/tmp/integration_debug.json")
    
    return result
```

## Performance Debugging

### Memory Profiling

```python
# Use memory_profiler
from memory_profiler import profile

@profile
def profile_revert_operation():
    """Profile memory usage of revert operation."""
    from drift.revert import RevertEngine
    
    engine = RevertEngine()
    
    # Create large test scenario
    options = RevertOptions(dry_run=True)
    result = engine.initiate_revert("test_hash", options)
    
    return result

# Run with: python -m memory_profiler script.py
```

### CPU Profiling

```python
import cProfile
import pstats

def profile_cpu_usage():
    """Profile CPU usage of revert operations."""
    
    profiler = cProfile.Profile()
    profiler.enable()
    
    # Run revert operation
    from drift.revert import RevertEngine
    engine = RevertEngine()
    result = engine.initiate_revert("test_hash", RevertOptions(dry_run=True))
    
    profiler.disable()
    
    # Save results
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.dump_stats('/tmp/revert_profile.prof')
    
    # Print top functions
    stats.print_stats(20)
    
    return result
```

### Performance Monitoring

```python
class PerformanceMonitor:
    """Monitor performance metrics during revert operations."""
    
    def __init__(self):
        self.metrics = []
        self.start_time = None
    
    def start_monitoring(self):
        """Start performance monitoring."""
        self.start_time = time.time()
        self.metrics = []
    
    def record_metric(self, name: str, value: float, unit: str = ""):
        """Record a performance metric."""
        self.metrics.append({
            "name": name,
            "value": value,
            "unit": unit,
            "timestamp": time.time() - self.start_time
        })
    
    def get_summary(self) -> dict:
        """Get performance summary."""
        if not self.metrics:
            return {}
        
        summary = {
            "total_duration": max(m["timestamp"] for m in self.metrics),
            "metrics_count": len(self.metrics),
            "metrics": self.metrics
        }
        
        return summary
    
    def save_report(self, filename: str):
        """Save performance report."""
        with open(filename, 'w') as f:
            json.dump(self.get_summary(), f, indent=2)
```

## Error Analysis

### Error Classification

```python
class ErrorAnalyzer:
    """Analyze and classify revert errors."""
    
    ERROR_CATEGORIES = {
        "validation": ["ValidationError", "TargetResolutionError"],
        "planning": ["PlanningError", "DependencyError"],
        "safety": ["SafetyError", "RiskAssessmentError"],
        "execution": ["ExecutionError", "CommandError"],
        "rollback": ["RollbackError", "RecoveryError"]
    }
    
    def analyze_error(self, error: Exception, context: dict) -> dict:
        """Analyze error and provide debugging information."""
        error_type = type(error).__name__
        
        # Classify error
        category = "unknown"
        for cat, types in self.ERROR_CATEGORIES.items():
            if error_type in types:
                category = cat
                break
        
        analysis = {
            "error_type": error_type,
            "category": category,
            "message": str(error),
            "context": context,
            "suggestions": self._get_suggestions(category, error, context)
        }
        
        return analysis
    
    def _get_suggestions(self, category: str, error: Exception, context: dict) -> list:
        """Get debugging suggestions based on error category."""
        suggestions = []
        
        if category == "validation":
            suggestions.extend([
                "Check if target snapshot exists",
                "Verify snapshot hash format",
                "Check revert options validity"
            ])
        elif category == "planning":
            suggestions.extend([
                "Check change detection logic",
                "Verify operation generation",
                "Check dependency calculation"
            ])
        elif category == "safety":
            suggestions.extend([
                "Check system prerequisites",
                "Verify risk assessment logic",
                "Check safety configuration"
            ])
        elif category == "execution":
            suggestions.extend([
                "Check command syntax",
                "Verify system permissions",
                "Check command availability"
            ])
        elif category == "rollback":
            suggestions.extend([
                "Check rollback command generation",
                "Verify rollback sequence",
                "Check system state consistency"
            ])
        
        return suggestions
```

## Development Workflows

### Debug Session Workflow

```bash
#!/bin/bash
# debug_session.sh - Start a debug session

# Set debug environment
export DRIFT_DEBUG=1
export DRIFT_LOG_LEVEL=DEBUG
export DRIFT_LOG_FILE=/tmp/drift-debug.log
export DRIFT_TRACE_OPERATIONS=1

# Create debug directory
mkdir -p /tmp/drift-debug
cd /tmp/drift-debug

# Start debug session
echo "Starting drift debug session..."
echo "Debug log: /tmp/drift-debug.log"
echo "Working directory: /tmp/drift-debug"

# Run drift with debugging
python -c "
from drift.revert import RevertEngine
from drift.revert.debug_tools import RevertDebugger

debugger = RevertDebugger()
debugger.set_breakpoint('operation_planning')
debugger.set_breakpoint('safety_validation')

engine = RevertEngine()
# Your debug code here
"
```

### Automated Debug Report

```python
def generate_debug_report(revert_id: str) -> str:
    """Generate comprehensive debug report for a revert operation."""
    
    report = {
        "revert_id": revert_id,
        "timestamp": datetime.utcnow().isoformat(),
        "system_info": {
            "platform": platform.platform(),
            "python_version": sys.version,
            "drift_version": get_drift_version()
        },
        "logs": [],
        "performance": {},
        "errors": [],
        "recommendations": []
    }
    
    # Collect logs
    log_file = f"/tmp/drift-{revert_id}.log"
    if os.path.exists(log_file):
        with open(log_file) as f:
            report["logs"] = f.readlines()
    
    # Collect performance data
    perf_file = f"/tmp/drift-{revert_id}-perf.json"
    if os.path.exists(perf_file):
        with open(perf_file) as f:
            report["performance"] = json.load(f)
    
    # Generate recommendations
    report["recommendations"] = generate_recommendations(report)
    
    # Save report
    report_file = f"/tmp/drift-debug-report-{revert_id}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    return report_file

def generate_recommendations(report: dict) -> list:
    """Generate debugging recommendations based on report data."""
    recommendations = []
    
    # Check for common issues
    if report["errors"]:
        recommendations.append("Review error logs for failure patterns")
    
    if report["performance"].get("duration", 0) > 300:
        recommendations.append("Consider optimizing operation planning for better performance")
    
    if len(report["logs"]) > 10000:
        recommendations.append("Reduce log verbosity for production use")
    
    return recommendations
```

This debugging guide provides comprehensive tools and techniques for developing and troubleshooting the drift revert system. Use these tools to identify issues, optimize performance, and ensure reliable operation of the revert functionality.