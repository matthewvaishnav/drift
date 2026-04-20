# Drift Revert Extension Guide

This guide explains how to extend the drift revert system to support new operation types and categories.

## Table of Contents

- [Overview](#overview)
- [Extension Architecture](#extension-architecture)
- [Adding New Categories](#adding-new-categories)
- [Implementing Operation Types](#implementing-operation-types)
- [Risk Assessment Integration](#risk-assessment-integration)
- [Command Generation](#command-generation)
- [Testing Extensions](#testing-extensions)
- [Best Practices](#best-practices)
- [Examples](#examples)

## Overview

The drift revert system is designed with extensibility in mind. You can add support for new system categories (like network interfaces, firewall rules, or custom applications) by implementing specific interfaces and following established patterns.

### Extension Points

The system provides several extension points:

1. **Change Detection**: Detect differences in new categories
2. **Operation Planning**: Convert changes to executable operations
3. **Risk Assessment**: Evaluate safety of new operation types
4. **Command Generation**: Generate system commands for operations
5. **Execution**: Handle special execution requirements

## Extension Architecture

### Plugin Interface

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from drift.revert.models import Operation, RiskLevel
from drift.models import Change

class RevertPlugin(ABC):
    """Base interface for revert system plugins."""
    
    @abstractmethod
    def get_supported_categories(self) -> List[str]:
        """Return list of categories this plugin supports."""
        pass
    
    @abstractmethod
    def generate_operations(self, changes: List[Change]) -> List[Operation]:
        """Convert changes to operations for this category."""
        pass
    
    @abstractmethod
    def assess_risk(self, operation: Operation) -> RiskLevel:
        """Assess risk level for an operation."""
        pass
    
    @abstractmethod
    def generate_command(self, operation: Operation) -> str:
        """Generate system command for an operation."""
        pass
    
    def get_dependencies(self, operation: Operation) -> List[str]:
        """Return list of operation IDs this operation depends on."""
        return []
    
    def get_rollback_command(self, operation: Operation) -> str:
        """Generate rollback command for failed operation."""
        return ""
```

### Registration System

```python
# Plugin registration
from drift.revert.plugins import register_plugin

@register_plugin
class NetworkInterfacePlugin(RevertPlugin):
    def get_supported_categories(self) -> List[str]:
        return ["network_interfaces"]
    
    # ... implement other methods
```

## Adding New Categories

### Step 1: Extend Change Detection

First, add change detection for your new category in the collectors:

```python
# drift/collectors/network.py
from drift.collectors import BaseCollector
from drift.models import Change

class NetworkInterfaceCollector(BaseCollector):
    """Collector for network interface configuration."""
    
    def collect(self) -> Dict[str, Any]:
        """Collect current network interface state."""
        interfaces = {}
        
        # Use ip command to get interface info
        result = subprocess.run(['ip', 'addr', 'show'], 
                              capture_output=True, text=True)
        
        # Parse output and build interface data
        for line in result.stdout.split('\n'):
            # Parse interface configuration
            pass
            
        return {
            'network_interfaces': interfaces
        }
    
    def detect_changes(self, before: Dict, after: Dict) -> List[Change]:
        """Detect changes in network interfaces."""
        changes = []
        
        before_interfaces = before.get('network_interfaces', {})
        after_interfaces = after.get('network_interfaces', {})
        
        # Detect added interfaces
        for name, config in after_interfaces.items():
            if name not in before_interfaces:
                changes.append(Change(
                    category='network_interfaces',
                    item=name,
                    action='added',
                    before=None,
                    after=config
                ))
        
        # Detect removed interfaces
        for name, config in before_interfaces.items():
            if name not in after_interfaces:
                changes.append(Change(
                    category='network_interfaces',
                    item=name,
                    action='removed',
                    before=config,
                    after=None
                ))
        
        # Detect modified interfaces
        for name in set(before_interfaces.keys()) & set(after_interfaces.keys()):
            if before_interfaces[name] != after_interfaces[name]:
                changes.append(Change(
                    category='network_interfaces',
                    item=name,
                    action='modified',
                    before=before_interfaces[name],
                    after=after_interfaces[name]
                ))
        
        return changes
```

### Step 2: Register the Collector

```python
# drift/collectors/__init__.py
from .network import NetworkInterfaceCollector

# Add to collector registry
COLLECTORS = {
    'network_interfaces': NetworkInterfaceCollector,
    # ... other collectors
}
```

## Implementing Operation Types

### Step 3: Create Plugin Implementation

```python
# drift/revert/plugins/network.py
from drift.revert.plugins import RevertPlugin
from drift.revert.models import Operation, RiskLevel
from drift.models import Change
import uuid

class NetworkInterfacePlugin(RevertPlugin):
    """Plugin for network interface operations."""
    
    def get_supported_categories(self) -> List[str]:
        return ["network_interfaces"]
    
    def generate_operations(self, changes: List[Change]) -> List[Operation]:
        """Convert network interface changes to operations."""
        operations = []
        
        for change in changes:
            if change.category != "network_interfaces":
                continue
                
            op_id = f"net_{uuid.uuid4().hex[:8]}"
            
            if change.action == "added":
                # Interface was added, need to remove it
                operations.append(Operation(
                    id=op_id,
                    category="network_interfaces",
                    action="remove",
                    target=change.item,
                    command="",  # Will be generated later
                    risk_level=RiskLevel.HIGH,  # Network changes are risky
                    description=f"Remove network interface {change.item}"
                ))
            
            elif change.action == "removed":
                # Interface was removed, need to add it back
                operations.append(Operation(
                    id=op_id,
                    category="network_interfaces",
                    action="add",
                    target=change.item,
                    command="",
                    risk_level=RiskLevel.HIGH,
                    description=f"Add network interface {change.item}"
                ))
            
            elif change.action == "modified":
                # Interface was modified, need to restore config
                operations.append(Operation(
                    id=op_id,
                    category="network_interfaces",
                    action="configure",
                    target=change.item,
                    command="",
                    risk_level=RiskLevel.MEDIUM,
                    description=f"Reconfigure network interface {change.item}"
                ))
        
        return operations
    
    def assess_risk(self, operation: Operation) -> RiskLevel:
        """Assess risk for network operations."""
        if operation.category != "network_interfaces":
            return RiskLevel.LOW
        
        # Network operations are generally high risk
        if operation.action in ["remove", "add"]:
            return RiskLevel.HIGH
        elif operation.action == "configure":
            return RiskLevel.MEDIUM
        
        return RiskLevel.LOW
    
    def generate_command(self, operation: Operation) -> str:
        """Generate ip command for network operations."""
        if operation.category != "network_interfaces":
            return ""
        
        interface = operation.target
        
        if operation.action == "remove":
            return f"ip link delete {interface}"
        elif operation.action == "add":
            # This would need interface config from change.before
            return f"ip link add {interface} type dummy"
        elif operation.action == "configure":
            # This would need specific configuration
            return f"ip addr flush dev {interface}"
        
        return ""
    
    def get_dependencies(self, operation: Operation) -> List[str]:
        """Network operations may have dependencies."""
        # Example: removing a bridge interface depends on 
        # removing member interfaces first
        return []
    
    def get_rollback_command(self, operation: Operation) -> str:
        """Generate rollback command."""
        if operation.action == "remove":
            return f"ip link add {operation.target} type dummy"
        elif operation.action == "add":
            return f"ip link delete {operation.target}"
        
        return ""
```

## Risk Assessment Integration

### Custom Risk Factors

```python
class NetworkRiskAssessor:
    """Specialized risk assessment for network operations."""
    
    def assess_network_risk(self, operation: Operation) -> RiskLevel:
        """Assess network-specific risks."""
        interface = operation.target
        
        # Critical interfaces (management, primary network)
        if interface in ['eth0', 'ens3', 'mgmt0']:
            return RiskLevel.CRITICAL
        
        # Virtual interfaces are safer
        if interface.startswith(('veth', 'docker', 'br-')):
            return RiskLevel.MEDIUM
        
        # Default network risk
        return RiskLevel.HIGH
    
    def get_risk_factors(self, operation: Operation) -> List[str]:
        """Get specific risk factors for network operations."""
        factors = []
        
        if operation.target in ['eth0', 'ens3']:
            factors.append("Primary network interface - may cause connectivity loss")
        
        if operation.action == "remove":
            factors.append("Interface removal may affect dependent services")
        
        return factors
```

## Command Generation

### Advanced Command Generation

```python
class NetworkCommandGenerator:
    """Generate network configuration commands."""
    
    def __init__(self):
        self.ip_cmd = "/sbin/ip"
    
    def generate_interface_command(self, operation: Operation, 
                                 config: Dict[str, Any]) -> str:
        """Generate detailed interface configuration command."""
        interface = operation.target
        
        if operation.action == "configure":
            commands = []
            
            # Set IP addresses
            if 'addresses' in config:
                for addr in config['addresses']:
                    commands.append(f"{self.ip_cmd} addr add {addr} dev {interface}")
            
            # Set interface state
            if config.get('state') == 'up':
                commands.append(f"{self.ip_cmd} link set {interface} up")
            else:
                commands.append(f"{self.ip_cmd} link set {interface} down")
            
            # Set MTU
            if 'mtu' in config:
                commands.append(f"{self.ip_cmd} link set {interface} mtu {config['mtu']}")
            
            return " && ".join(commands)
        
        return ""
    
    def validate_command(self, command: str) -> bool:
        """Validate generated command for safety."""
        # Check for dangerous patterns
        dangerous_patterns = [
            'rm -rf',
            'dd if=',
            'mkfs',
            '> /dev/'
        ]
        
        for pattern in dangerous_patterns:
            if pattern in command:
                return False
        
        return True
```

## Testing Extensions

### Unit Tests for Plugins

```python
# tests/test_network_plugin.py
import pytest
from drift.revert.plugins.network import NetworkInterfacePlugin
from drift.models import Change
from drift.revert.models import RiskLevel

class TestNetworkInterfacePlugin:
    
    def setup_method(self):
        self.plugin = NetworkInterfacePlugin()
    
    def test_supported_categories(self):
        categories = self.plugin.get_supported_categories()
        assert "network_interfaces" in categories
    
    def test_generate_operations_added_interface(self):
        change = Change(
            category="network_interfaces",
            item="eth1",
            action="added",
            before=None,
            after={"state": "up", "addresses": ["192.168.1.10/24"]}
        )
        
        operations = self.plugin.generate_operations([change])
        
        assert len(operations) == 1
        assert operations[0].action == "remove"
        assert operations[0].target == "eth1"
        assert operations[0].risk_level == RiskLevel.HIGH
    
    def test_risk_assessment(self):
        from drift.revert.models import Operation
        
        op = Operation(
            id="test",
            category="network_interfaces",
            action="remove",
            target="eth0",
            command="ip link delete eth0",
            risk_level=RiskLevel.LOW
        )
        
        risk = self.plugin.assess_risk(op)
        assert risk == RiskLevel.HIGH
    
    def test_command_generation(self):
        from drift.revert.models import Operation
        
        op = Operation(
            id="test",
            category="network_interfaces",
            action="remove",
            target="eth1",
            command="",
            risk_level=RiskLevel.HIGH
        )
        
        command = self.plugin.generate_command(op)
        assert "ip link delete eth1" in command
```

### Integration Tests

```python
# tests/integration/test_network_revert.py
import pytest
from drift.revert import RevertEngine, RevertOptions
from tests.fixtures import create_test_snapshots

class TestNetworkRevert:
    
    def test_network_interface_revert(self, test_snapshots):
        """Test end-to-end network interface revert."""
        current, target = test_snapshots
        
        # Add network interface change
        current.data['network_interfaces'] = {
            'eth1': {'state': 'up', 'addresses': ['192.168.1.10/24']}
        }
        target.data['network_interfaces'] = {}
        
        engine = RevertEngine()
        options = RevertOptions(dry_run=True)
        
        result = engine.initiate_revert(target.hash, options)
        
        assert result.success
        assert len(result.operation_plan.batches) > 0
        
        # Check that network operation was planned
        operations = []
        for batch in result.operation_plan.batches:
            operations.extend(batch.operations)
        
        network_ops = [op for op in operations if op.category == "network_interfaces"]
        assert len(network_ops) == 1
        assert network_ops[0].action == "remove"
```

## Best Practices

### 1. Safety First

- Always assess operations as high risk initially
- Implement comprehensive validation
- Provide detailed rollback commands
- Test thoroughly in isolated environments

### 2. Command Safety

```python
def validate_command_safety(command: str) -> List[str]:
    """Validate command for safety issues."""
    issues = []
    
    # Check for destructive patterns
    destructive_patterns = [
        r'rm\s+-rf\s+/',
        r'dd\s+if=.*of=/dev/',
        r'mkfs\.',
        r'fdisk\s+/dev/',
        r'parted\s+/dev/'
    ]
    
    for pattern in destructive_patterns:
        if re.search(pattern, command):
            issues.append(f"Potentially destructive command pattern: {pattern}")
    
    # Check for privilege escalation
    if 'sudo' in command or 'su -' in command:
        issues.append("Command requires privilege escalation")
    
    return issues
```

### 3. Dependency Management

```python
def calculate_dependencies(self, operations: List[Operation]) -> Dict[str, List[str]]:
    """Calculate operation dependencies."""
    dependencies = {}
    
    for op in operations:
        deps = []
        
        # Network interfaces depend on bridge removal
        if op.category == "network_interfaces" and op.action == "remove":
            for other_op in operations:
                if (other_op.category == "bridges" and 
                    other_op.action == "remove" and
                    op.target in other_op.get_member_interfaces()):
                    deps.append(other_op.id)
        
        dependencies[op.id] = deps
    
    return dependencies
```

### 4. Error Handling

```python
class NetworkOperationError(Exception):
    """Network-specific operation error."""
    
    def __init__(self, message: str, interface: str, command: str):
        super().__init__(message)
        self.interface = interface
        self.command = command

def execute_network_operation(operation: Operation) -> ExecutionResult:
    """Execute network operation with proper error handling."""
    try:
        result = subprocess.run(
            operation.command.split(),
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise NetworkOperationError(
                f"Network command failed: {result.stderr}",
                operation.target,
                operation.command
            )
        
        return ExecutionResult(success=True)
        
    except subprocess.TimeoutExpired:
        raise NetworkOperationError(
            "Network operation timed out",
            operation.target,
            operation.command
        )
    except Exception as e:
        raise NetworkOperationError(
            f"Unexpected error: {str(e)}",
            operation.target,
            operation.command
        )
```

## Examples

### Example 1: Firewall Rules Plugin

```python
class FirewallPlugin(RevertPlugin):
    """Plugin for iptables/firewall rules."""
    
    def get_supported_categories(self) -> List[str]:
        return ["firewall_rules"]
    
    def generate_operations(self, changes: List[Change]) -> List[Operation]:
        operations = []
        
        for change in changes:
            if change.action == "added":
                # Rule was added, need to remove it
                operations.append(Operation(
                    id=f"fw_{uuid.uuid4().hex[:8]}",
                    category="firewall_rules",
                    action="delete",
                    target=change.item,
                    command=f"iptables -D {change.after['chain']} {change.after['rule']}",
                    risk_level=RiskLevel.HIGH,
                    description=f"Remove firewall rule {change.item}"
                ))
        
        return operations
    
    def assess_risk(self, operation: Operation) -> RiskLevel:
        # Firewall changes are always high risk
        return RiskLevel.HIGH
```

### Example 2: Custom Application Plugin

```python
class CustomAppPlugin(RevertPlugin):
    """Plugin for custom application configuration."""
    
    def get_supported_categories(self) -> List[str]:
        return ["custom_apps"]
    
    def generate_operations(self, changes: List[Change]) -> List[Operation]:
        operations = []
        
        for change in changes:
            if change.category == "custom_apps":
                operations.append(Operation(
                    id=f"app_{uuid.uuid4().hex[:8]}",
                    category="custom_apps",
                    action="reconfigure",
                    target=change.item,
                    command=f"/opt/{change.item}/bin/restore-config {change.before['config_hash']}",
                    risk_level=RiskLevel.MEDIUM,
                    description=f"Restore {change.item} configuration"
                ))
        
        return operations
```

### Example 3: Database Plugin

```python
class DatabasePlugin(RevertPlugin):
    """Plugin for database operations."""
    
    def get_supported_categories(self) -> List[str]:
        return ["databases", "database_users"]
    
    def assess_risk(self, operation: Operation) -> RiskLevel:
        # Database operations are critical
        if operation.action in ["drop", "delete"]:
            return RiskLevel.CRITICAL
        return RiskLevel.HIGH
    
    def generate_command(self, operation: Operation) -> str:
        if operation.category == "databases":
            if operation.action == "create":
                return f"createdb {operation.target}"
            elif operation.action == "drop":
                return f"dropdb {operation.target}"
        
        return ""
```

## Plugin Registration and Loading

### Automatic Plugin Discovery

```python
# drift/revert/plugins/__init__.py
import importlib
import pkgutil
from typing import Dict, Type

_plugins: Dict[str, Type[RevertPlugin]] = {}

def register_plugin(plugin_class: Type[RevertPlugin]):
    """Register a revert plugin."""
    plugin_instance = plugin_class()
    for category in plugin_instance.get_supported_categories():
        _plugins[category] = plugin_class
    return plugin_class

def get_plugin_for_category(category: str) -> RevertPlugin:
    """Get plugin instance for a category."""
    if category in _plugins:
        return _plugins[category]()
    return None

def load_plugins():
    """Automatically load all plugins in the plugins directory."""
    import drift.revert.plugins
    
    for importer, modname, ispkg in pkgutil.iter_modules(
        drift.revert.plugins.__path__, 
        drift.revert.plugins.__name__ + "."
    ):
        importlib.import_module(modname)

# Load plugins on import
load_plugins()
```

This extension guide provides a comprehensive framework for adding new operation types to the drift revert system. Follow these patterns and best practices to ensure your extensions are safe, reliable, and well-integrated with the existing system.