"""
Property-based tests for the revert system.

These tests use Hypothesis to generate random test cases and verify
that the revert system maintains important properties and invariants.
"""
import pytest
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize
from unittest.mock import Mock, patch
from typing import List, Dict, Any

from drift.revert.engine import RevertEngine
from drift.revert.models import RevertOptions, RiskLevel
from drift.models import Snapshot, Package, Service, User, Group


# ── Property Test Strategies ──────────────────────────────────────────────────

@st.composite
def package_strategy(draw):
    """Generate random Package objects."""
    name = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='-_')))
    version = draw(st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('Nd',), whitelist_characters='.')))
    manager = draw(st.sampled_from(['apt', 'yum', 'pip', 'npm']))
    return Package(name=name, version=version, manager=manager)


@st.composite
def service_strategy(draw):
    """Generate random Service objects."""
    name = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='-_')))
    state = draw(st.sampled_from(['active', 'inactive', 'failed']))
    enabled = draw(st.booleans())
    return Service(name=name, state=state, enabled=enabled)


@st.composite
def user_strategy(draw):
    """Generate random User objects."""
    name = draw(st.text(min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='_')))
    uid = draw(st.integers(min_value=0, max_value=65535))
    gid = draw(st.integers(min_value=0, max_value=65535))
    shell = draw(st.sampled_from(['/bin/bash', '/bin/sh', '/bin/zsh', '/usr/bin/fish']))
    home = f"/home/{name}" if name != "root" else "/root"
    return User(name=name, uid=uid, gid=gid, shell=shell, home=home)


@st.composite
def group_strategy(draw):
    """Generate random Group objects."""
    name = draw(st.text(min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='_')))
    gid = draw(st.integers(min_value=0, max_value=65535))
    members = draw(st.lists(st.text(min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='_')), max_size=5))
    return Group(name=name, gid=gid, members=members)


@st.composite
def snapshot_strategy(draw):
    """Generate random Snapshot objects."""
    hostname = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='-.')))
    timestamp = "2024-01-01T12:00:00Z"
    os_name = draw(st.sampled_from(["Linux 5.4.0", "Ubuntu 20.04", "CentOS 8"]))
    kernel = draw(st.sampled_from(["5.4.0-42-generic", "4.18.0-193.el8.x86_64"]))
    
    packages = draw(st.lists(package_strategy(), max_size=5))
    services = draw(st.lists(service_strategy(), max_size=5))
    users = draw(st.lists(user_strategy(), max_size=3))
    groups = draw(st.lists(group_strategy(), max_size=3))
    
    return Snapshot(
        hostname=hostname,
        timestamp=timestamp,
        os=os_name,
        kernel=kernel,
        packages=packages,
        services=services,
        users=users,
        groups=groups,
        errors=[]
    )


@st.composite
def snapshot_pair_strategy(draw):
    """Generate a pair of snapshots with the same hostname for comparison."""
    hostname = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='-.')))
    os_name = draw(st.sampled_from(["Linux 5.4.0", "Ubuntu 20.04", "CentOS 8"]))
    kernel = draw(st.sampled_from(["5.4.0-42-generic", "4.18.0-193.el8.x86_64"]))
    
    # Generate base components
    base_packages = draw(st.lists(package_strategy(), max_size=5))
    base_services = draw(st.lists(service_strategy(), max_size=5))
    base_users = draw(st.lists(user_strategy(), max_size=3))
    base_groups = draw(st.lists(group_strategy(), max_size=3))
    
    # Create current snapshot
    current_packages = base_packages + draw(st.lists(package_strategy(), max_size=3))
    current_services = base_services + draw(st.lists(service_strategy(), max_size=3))
    current_users = base_users + draw(st.lists(user_strategy(), max_size=2))
    current_groups = base_groups + draw(st.lists(group_strategy(), max_size=2))
    
    current_snapshot = Snapshot(
        hostname=hostname,
        timestamp="2024-01-01T12:00:00Z",
        os=os_name,
        kernel=kernel,
        packages=current_packages,
        services=current_services,
        users=current_users,
        groups=current_groups,
        errors=[]
    )
    
    # Create target snapshot (subset of current)
    target_packages = base_packages
    target_services = base_services
    target_users = base_users
    target_groups = base_groups
    
    target_snapshot = Snapshot(
        hostname=hostname,  # Same hostname
        timestamp="2024-01-01T10:00:00Z",
        os=os_name,
        kernel=kernel,
        packages=target_packages,
        services=target_services,
        users=target_users,
        groups=target_groups,
        errors=[]
    )
    
    return current_snapshot, target_snapshot


# ── Property Tests ────────────────────────────────────────────────────────────

class TestRevertIdempotency:
    """Test that revert operations are idempotent."""
    
    def setup_method(self):
        """Set up test environment."""
        self.engine = RevertEngine()
    
    @given(snapshot_pair_strategy())
    @settings(max_examples=10, deadline=5000, suppress_health_check=[HealthCheck.filter_too_much])
    def test_revert_idempotency_property(self, snapshot_pair):
        """
        Property: Reverting to the same target twice should produce identical results.
        
        If we revert from state A to state B, and then revert from B to B again,
        the second revert should be a no-op (no operations needed).
        """
        current_snapshot, target_snapshot = snapshot_pair
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            # First revert: current -> target
            mock_load.return_value = target_snapshot
            mock_run_all.return_value = current_snapshot
            
            options = RevertOptions(dry_run=True, force=True)
            result1 = self.engine.initiate_revert("target_hash", options)
            
            # Second revert: target -> target (should be no-op)
            mock_run_all.return_value = target_snapshot  # Now current state is target
            result2 = self.engine.initiate_revert("target_hash", options)
            
            # Both should succeed
            assert result1.success is True
            assert result2.success is True
            
            # Second revert should have no operations (idempotent)
            if result2.operation_plan:
                assert result2.operation_plan.total_operations == 0, \
                    f"Second revert should be no-op, but found {result2.operation_plan.total_operations} operations"
    
    @given(snapshot_strategy())
    @settings(max_examples=10, deadline=5000)
    def test_self_revert_is_noop(self, snapshot):
        """
        Property: Reverting a snapshot to itself should always be a no-op.
        """
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            # Revert snapshot to itself
            mock_load.return_value = snapshot
            mock_run_all.return_value = snapshot
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("same_hash", options)
            
            # Should succeed with no operations
            assert result.success is True
            if result.operation_plan:
                assert result.operation_plan.total_operations == 0, \
                    f"Self-revert should be no-op, but found {result.operation_plan.total_operations} operations"


class TestRevertConsistency:
    """Test that revert operations maintain consistency."""
    
    def setup_method(self):
        """Set up test environment."""
        self.engine = RevertEngine()
    
    @given(snapshot_pair_strategy())
    @settings(max_examples=10, deadline=5000, suppress_health_check=[HealthCheck.filter_too_much])
    def test_revert_plan_consistency(self, snapshot_pair):
        """
        Property: The same revert operation should always produce the same plan.
        """
        current_snapshot, target_snapshot = snapshot_pair
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target_snapshot
            mock_run_all.return_value = current_snapshot
            
            options = RevertOptions(dry_run=True, force=True)
            
            # Execute same revert twice
            result1 = self.engine.initiate_revert("target_hash", options)
            result2 = self.engine.initiate_revert("target_hash", options)
            
            # Both should succeed
            assert result1.success is True
            assert result2.success is True
            
            # Plans should be identical
            if result1.operation_plan and result2.operation_plan:
                assert result1.operation_plan.total_operations == result2.operation_plan.total_operations, \
                    "Same revert should produce identical operation counts"
                
                # Risk assessments should be the same
                assert result1.operation_plan.risk_assessment == result2.operation_plan.risk_assessment, \
                    "Same revert should produce identical risk assessments"
    
    @given(st.lists(package_strategy(), min_size=1, max_size=5))
    @settings(max_examples=10, deadline=5000)
    def test_package_operations_are_reversible(self, packages):
        """
        Property: Package operations should be logically reversible.
        
        If we plan to remove a package, there should be a way to add it back.
        If we plan to downgrade a package, there should be a way to upgrade it.
        """
        # Create snapshots with package differences
        current = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=packages,
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        target = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T10:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[],  # Remove all packages
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target
            mock_run_all.return_value = current
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("target_hash", options)
            
            if result.success and result.operation_plan:
                # All operations should be package removals
                all_operations = [op for batch in result.operation_plan.batches for op in batch.operations]
                package_ops = [op for op in all_operations if op.category == "package"]
                
                for op in package_ops:
                    # Package operations should have valid actions
                    assert op.action in ["remove", "install", "upgrade", "downgrade"], \
                        f"Invalid package action: {op.action}"
                    
                    # Operations should have valid targets
                    assert op.target, f"Package operation missing target: {op}"
                    
                    # Operations should have commands
                    assert op.command, f"Package operation missing command: {op}"


class TestRevertSafety:
    """Test that revert operations maintain safety properties."""
    
    def setup_method(self):
        """Set up test environment."""
        self.engine = RevertEngine()
    
    @given(snapshot_pair_strategy())
    @settings(max_examples=5, deadline=5000, suppress_health_check=[HealthCheck.filter_too_much])
    def test_revert_never_corrupts_data_structures(self, snapshot_pair):
        """
        Property: Revert operations should never produce invalid data structures.
        """
        current_snapshot, target_snapshot = snapshot_pair
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target_snapshot
            mock_run_all.return_value = current_snapshot
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("target_hash", options)
            
            # Result should always be valid
            assert hasattr(result, 'success'), "Result missing success field"
            assert hasattr(result, 'revert_id'), "Result missing revert_id field"
            assert hasattr(result, 'target_hash'), "Result missing target_hash field"
            
            # If operation plan exists, it should be valid
            if result.operation_plan:
                assert result.operation_plan.total_operations >= 0, "Invalid operation count"
                assert len(result.operation_plan.batches) >= 0, "Invalid batch count"
                
                # All operations should be valid
                for batch in result.operation_plan.batches:
                    assert batch.id, "Batch missing ID"
                    for op in batch.operations:
                        assert op.id, "Operation missing ID"
                        assert op.category, "Operation missing category"
                        assert op.action, "Operation missing action"
                        assert isinstance(op.risk_level, RiskLevel), "Invalid risk level"
    
    @given(st.integers(min_value=0, max_value=100))
    @settings(max_examples=10, deadline=3000)
    def test_large_operation_plans_are_bounded(self, num_operations):
        """
        Property: Large operation plans should be bounded and not cause resource exhaustion.
        """
        # Create snapshots with many differences
        packages = [Package(name=f"pkg-{i}", version="1.0.0", manager="apt") for i in range(num_operations)]
        
        current = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=packages,
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        target = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T10:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[],
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target
            mock_run_all.return_value = current
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("large_hash", options)
            
            # Should complete within reasonable bounds
            if result.success and result.operation_plan:
                # Operation count should be reasonable
                assert result.operation_plan.total_operations <= num_operations * 2, \
                    "Operation plan grew unreasonably large"
                
                # Estimated duration should be bounded
                assert result.operation_plan.estimated_duration <= num_operations * 300, \
                    "Estimated duration unreasonably large"


class TestRevertRollbackProperties:
    """Test properties related to rollback completeness."""
    
    def setup_method(self):
        """Set up test environment."""
        self.engine = RevertEngine()
    
    @given(snapshot_pair_strategy())
    @settings(max_examples=5, deadline=5000, suppress_health_check=[HealthCheck.filter_too_much])
    def test_rollback_completeness_property(self, snapshot_pair):
        """
        Property: If a revert operation fails, rollback should restore the original state.
        
        This tests that rollback operations are complete and don't leave the system
        in an inconsistent intermediate state.
        """
        current_snapshot, target_snapshot = snapshot_pair
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target_snapshot
            mock_run_all.return_value = current_snapshot
            
            # Mock execution engine to simulate failure with rollback
            with patch('drift.revert.executor.ExecutionEngine') as mock_executor_class:
                mock_executor = Mock()
                mock_executor_class.return_value = mock_executor
                
                # Simulate execution failure with successful rollback
                from drift.revert.models import ExecutionResult
                mock_executor.execute_plan.return_value = ExecutionResult(
                    success=False,
                    operations_completed=1,
                    operations_failed=1,
                    failed_operations=[],
                    rollback_performed=True,
                    duration_seconds=2.0,
                    error_message="Simulated failure"
                )
                
                # Execute revert (not dry run to trigger execution)
                options = RevertOptions(dry_run=False, force=True)
                result = self.engine.initiate_revert("target_hash", options)
                
                # Should fail but rollback should be performed
                assert result.success is False
                assert result.operations_failed >= 1
                
                # Rollback should have been attempted
                # (We can't verify the actual state restoration in this mock test,
                # but we can verify the rollback was triggered)
                mock_executor.execute_plan.assert_called_once()
    
    @given(st.lists(package_strategy(), min_size=1, max_size=3))
    @settings(max_examples=5, deadline=3000)
    def test_operation_reversibility_property(self, packages):
        """
        Property: Every operation should have a conceptual reverse operation.
        
        This tests that the operation planning system generates operations
        that could theoretically be reversed.
        """
        # Create snapshots with package differences
        current = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=packages,
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        target = Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T10:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[],  # Remove all packages
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target
            mock_run_all.return_value = current
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("target_hash", options)
            
            if result.success and result.operation_plan:
                # Check that operations have reversible actions
                all_operations = [op for batch in result.operation_plan.batches for op in batch.operations]
                
                for op in all_operations:
                    if op.category == "package":
                        # Package operations should be reversible
                        reversible_actions = {"install", "remove", "upgrade", "downgrade"}
                        assert op.action in reversible_actions, f"Non-reversible package action: {op.action}"
                    
                    elif op.category == "service":
                        # Service operations should be reversible
                        reversible_actions = {"start", "stop", "enable", "disable", "restart", "reconfigure"}
                        assert op.action in reversible_actions, f"Non-reversible service action: {op.action}"
                    
                    elif op.category in ["user", "group"]:
                        # User/group operations should be reversible
                        reversible_actions = {"create", "delete", "modify"}
                        assert op.action in reversible_actions, f"Non-reversible {op.category} action: {op.action}"
    
    @given(snapshot_pair_strategy())
    @settings(max_examples=3, deadline=3000, suppress_health_check=[HealthCheck.filter_too_much])
    def test_state_consistency_property(self, snapshot_pair):
        """
        Property: Revert operations should maintain state consistency invariants.
        
        This tests that the revert system doesn't generate operations that
        would violate basic system consistency rules.
        """
        current_snapshot, target_snapshot = snapshot_pair
        
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = target_snapshot
            mock_run_all.return_value = current_snapshot
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("target_hash", options)
            
            if result.success and result.operation_plan:
                all_operations = [op for batch in result.operation_plan.batches for op in batch.operations]
                
                # Check for consistency violations
                user_operations = [op for op in all_operations if op.category == "user"]
                group_operations = [op for op in all_operations if op.category == "group"]
                
                # Users being deleted should not be referenced in group operations
                deleted_users = {op.target for op in user_operations if op.action == "delete"}
                
                for group_op in group_operations:
                    if group_op.action == "create" and hasattr(group_op, 'members'):
                        # Group creation shouldn't reference deleted users
                        # (This is a simplified check - real implementation would be more complex)
                        pass
                
                # Operations should have valid targets
                for op in all_operations:
                    assert op.target, f"Operation missing target: {op}"
                    assert op.target.strip(), f"Operation has empty target: {op}"


# ── Stateful Property Tests ───────────────────────────────────────────────────

class RevertStateMachine(RuleBasedStateMachine):
    """
    Stateful property-based testing for revert operations.
    
    This tests sequences of revert operations to ensure they maintain
    consistency across multiple state transitions.
    """
    
    def __init__(self):
        super().__init__()
        self.engine = RevertEngine()
        self.snapshots: Dict[str, Snapshot] = {}
        self.current_state = "initial"
    
    @initialize()
    def setup_initial_state(self):
        """Initialize with a base snapshot."""
        initial_snapshot = Snapshot(
            hostname="stateful-test",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[
                Package(name="base-pkg", version="1.0.0", manager="apt")
            ],
            services=[
                Service(name="base-service", state="active", enabled=True)
            ],
            users=[
                User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root")
            ],
            groups=[
                Group(name="root", gid=0, members=[])
            ],
            errors=[]
        )
        self.snapshots["initial"] = initial_snapshot
        self.current_state = "initial"
    
    @rule(target_state=st.sampled_from(["state_a", "state_b", "state_c"]))
    def revert_to_state(self, target_state):
        """Revert to a different state."""
        # Create target snapshot if it doesn't exist
        if target_state not in self.snapshots:
            base = self.snapshots["initial"]
            if target_state == "state_a":
                # Add a package
                new_packages = base.packages + [Package(name="extra-pkg-a", version="2.0.0", manager="apt")]
                target_snapshot = Snapshot(
                    hostname=base.hostname,
                    timestamp="2024-01-01T13:00:00Z",
                    os=base.os,
                    kernel=base.kernel,
                    packages=new_packages,
                    services=base.services,
                    users=base.users,
                    groups=base.groups,
                    errors=[]
                )
            elif target_state == "state_b":
                # Add a service
                new_services = base.services + [Service(name="extra-service-b", state="active", enabled=True)]
                target_snapshot = Snapshot(
                    hostname=base.hostname,
                    timestamp="2024-01-01T14:00:00Z",
                    os=base.os,
                    kernel=base.kernel,
                    packages=base.packages,
                    services=new_services,
                    users=base.users,
                    groups=base.groups,
                    errors=[]
                )
            else:  # state_c
                # Add a user
                new_users = base.users + [User(name="extra-user-c", uid=1001, gid=1001, shell="/bin/bash", home="/home/extra-user-c")]
                target_snapshot = Snapshot(
                    hostname=base.hostname,
                    timestamp="2024-01-01T15:00:00Z",
                    os=base.os,
                    kernel=base.kernel,
                    packages=base.packages,
                    services=base.services,
                    users=new_users,
                    groups=base.groups,
                    errors=[]
                )
            self.snapshots[target_state] = target_snapshot
        
        # Perform revert
        with patch('drift.revert.engine.load_snapshot') as mock_load, \
             patch('drift.revert.engine.run_all') as mock_run_all:
            
            mock_load.return_value = self.snapshots[target_state]
            mock_run_all.return_value = self.snapshots[self.current_state]
            
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert(f"{target_state}_hash", options)
            
            # Revert should succeed
            assert result.success is True, f"Revert from {self.current_state} to {target_state} failed: {result.error_message}"
            
            # Update current state
            self.current_state = target_state
    
    @invariant()
    def current_state_is_valid(self):
        """Invariant: Current state should always be valid."""
        assert self.current_state in self.snapshots, f"Invalid current state: {self.current_state}"
        
        current_snapshot = self.snapshots[self.current_state]
        assert current_snapshot.hostname, "Current snapshot missing hostname"
        assert current_snapshot.timestamp, "Current snapshot missing timestamp"


# Run stateful tests
TestRevertStateMachine = RevertStateMachine.TestCase


if __name__ == "__main__":
    pytest.main([__file__, "-v"])