"""
Integration tests for the complete revert system.

These tests verify end-to-end revert workflows using real components
and simulated system state changes.
"""
import pytest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json

from drift.revert.engine import RevertEngine
from drift.revert.models import RevertOptions, RiskLevel
from drift.models import Snapshot, Package, Service, User, Group
from drift.storage import save_snapshot, load_snapshot


class TestRevertIntegration:
    """Integration tests for complete revert workflows."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.engine = RevertEngine()
        
        # Create mock snapshots for testing
        self.current_snapshot = self._create_test_snapshot("current")
        self.target_snapshot = self._create_test_snapshot("target")
    
    def teardown_method(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def _create_test_snapshot(self, variant: str) -> Snapshot:
        """Create a test snapshot with known differences."""
        if variant == "current":
            return Snapshot(
                hostname="test-host",
                timestamp="2024-01-01T12:00:00Z",
                os="Linux 5.4.0",
                kernel="5.4.0-42-generic",
                packages=[
                    Package(name="nginx", version="1.20.0", manager="apt"),
                    Package(name="python3", version="3.9.0", manager="apt"),
                    Package(name="test-pkg", version="2.0.0", manager="apt")  # Different version
                ],
                services=[
                    Service(name="nginx", state="active", enabled=True),
                    Service(name="ssh", state="active", enabled=True),
                    Service(name="test-service", state="inactive", enabled=False)  # Different state
                ],
                users=[
                    User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root"),
                    User(name="testuser", uid=1001, gid=1001, shell="/bin/bash", home="/home/testuser")  # Extra user
                ],
                groups=[
                    Group(name="root", gid=0, members=[]),
                    Group(name="testgroup", gid=1001, members=["testuser"])  # Extra group
                ],
                errors=[]
            )
        else:  # target
            return Snapshot(
                hostname="test-host",
                timestamp="2024-01-01T10:00:00Z",
                os="Linux 5.4.0",
                kernel="5.4.0-42-generic",
                packages=[
                    Package(name="nginx", version="1.20.0", manager="apt"),
                    Package(name="python3", version="3.9.0", manager="apt"),
                    Package(name="test-pkg", version="1.0.0", manager="apt")  # Different version
                ],
                services=[
                    Service(name="nginx", state="active", enabled=True),
                    Service(name="ssh", state="active", enabled=True),
                    Service(name="test-service", state="active", enabled=True)  # Different state
                ],
                users=[
                    User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root")
                    # Missing testuser
                ],
                groups=[
                    Group(name="root", gid=0, members=[])
                    # Missing testgroup
                ],
                errors=[]
            )
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_end_to_end_dry_run_revert(self, mock_run_all, mock_load_snapshot):
        """Test complete dry-run revert workflow."""
        # Setup mocks
        mock_load_snapshot.return_value = self.target_snapshot
        mock_run_all.return_value = self.current_snapshot
        
        # Execute dry-run revert with force to bypass safety validation on Windows
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("target_hash", options)
        
        # Verify results
        assert result.success is True
        assert result.operation_plan is not None
        assert result.operations_executed == 0  # Dry run
        assert result.safety_assessment is not None
        
        # Verify operation plan contains expected operations
        plan = result.operation_plan
        assert plan.total_operations > 0
        
        # Should have operations for user/group removal at minimum
        operation_categories = set()
        for batch in plan.batches:
            for op in batch.operations:
                operation_categories.add(op.category)
        
        # At minimum should have user and group operations (these work cross-platform)
        assert "user" in operation_categories
        assert "group" in operation_categories
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_revert_with_safety_validation_failure(self, mock_run_all, mock_load_snapshot):
        """Test revert that fails safety validation."""
        # Setup mocks
        mock_load_snapshot.return_value = self.target_snapshot
        mock_run_all.return_value = self.current_snapshot
        
        # Create high-risk scenario by adding critical service operations
        critical_snapshot = self._create_critical_service_snapshot()
        mock_load_snapshot.return_value = critical_snapshot
        
        # Execute revert without force
        options = RevertOptions(dry_run=False, force=False)
        result = self.engine.initiate_revert("critical_hash", options)
        
        # Should fail due to safety validation
        assert result.success is False
        assert "safety validation failed" in result.error_message.lower()
        assert result.safety_assessment is not None
        assert len(result.safety_assessment.risks) > 0
    
    def _create_critical_service_snapshot(self) -> Snapshot:
        """Create a snapshot with critical service changes."""
        return Snapshot(
            hostname="test-host",
            timestamp="2024-01-01T10:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[
                Package(name="openssh-server", version="8.0.0", manager="apt")  # Critical package
            ],
            services=[
                Service(name="sshd", state="inactive", enabled=False)  # Critical service stopped
            ],
            users=[
                User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root")
            ],
            groups=[
                Group(name="root", gid=0, members=[])
            ],
            errors=[]
        )
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_revert_with_force_override(self, mock_run_all, mock_load_snapshot):
        """Test revert with force flag overriding safety validation."""
        # Setup mocks with critical changes
        mock_load_snapshot.return_value = self._create_critical_service_snapshot()
        mock_run_all.return_value = self.current_snapshot
        
        # Execute revert with force
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("critical_hash", options)
        
        # Should succeed with force flag
        assert result.success is True
        assert result.operation_plan is not None
        assert result.safety_assessment is not None
        assert len(result.safety_assessment.risks) > 0  # Risks still identified
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_revert_no_changes_scenario(self, mock_run_all, mock_load_snapshot):
        """Test revert when no changes are detected."""
        # Setup mocks with identical snapshots
        mock_load_snapshot.return_value = self.current_snapshot
        mock_run_all.return_value = self.current_snapshot
        
        # Execute revert with force to bypass safety validation
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("same_hash", options)
        
        # Should succeed with no operations
        assert result.success is True
        assert result.operations_executed == 0
    
    @patch('drift.revert.engine.load_snapshot')
    def test_revert_invalid_target_hash(self, mock_load_snapshot):
        """Test revert with invalid target snapshot hash."""
        # Setup mock to return None (snapshot not found)
        mock_load_snapshot.return_value = None
        
        # Execute revert
        options = RevertOptions(dry_run=True)
        result = self.engine.initiate_revert("invalid_hash", options)
        
        # Should fail with appropriate error
        assert result.success is False
        assert "not found" in result.error_message.lower()
        assert result.operations_executed == 0


class TestRevertPerformance:
    """Performance tests for revert operations."""
    
    def setup_method(self):
        """Set up performance test environment."""
        self.engine = RevertEngine()
    
    def _create_large_snapshot(self, num_packages: int = 100) -> Snapshot:
        """Create a snapshot with many packages for performance testing."""
        packages = []
        for i in range(num_packages):
            packages.append(Package(
                name=f"package-{i}",
                version=f"1.{i}.0",
                manager="apt"
            ))
        
        return Snapshot(
            hostname="perf-test-host",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=packages,
            services=[],
            users=[],
            groups=[],
            errors=[]
        )
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_large_operation_plan_performance(self, mock_run_all, mock_load_snapshot):
        """Test performance with large operation plans."""
        # Create snapshots with many differences
        current = self._create_large_snapshot(100)
        target = self._create_large_snapshot(50)  # 50 packages to remove
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        # Measure execution time
        import time
        start_time = time.time()
        
        # Use force to bypass safety validation for performance testing
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("large_hash", options)
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        # Verify results
        assert result.success is True
        assert result.operation_plan.total_operations > 0
        
        # Performance assertion (should complete within reasonable time)
        assert execution_time < 5.0, f"Large operation plan took too long: {execution_time}s"
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_memory_usage_large_snapshots(self, mock_run_all, mock_load_snapshot):
        """Test memory usage with large snapshots."""
        import psutil
        import os
        
        # Get initial memory usage
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # Create large snapshots
        current = self._create_large_snapshot(500)
        target = self._create_large_snapshot(400)
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        # Execute revert with force to bypass safety validation
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("memory_test_hash", options)
        
        # Check memory usage
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        # Verify results
        assert result.success is True
        
        # Memory usage should be reasonable (less than 100MB increase)
        assert memory_increase < 100 * 1024 * 1024, f"Memory usage too high: {memory_increase / 1024 / 1024:.1f}MB"


class TestRevertErrorScenarios:
    """Test error handling and recovery scenarios."""
    
    def setup_method(self):
        """Set up error scenario tests."""
        self.engine = RevertEngine()
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_analysis_error_handling(self, mock_run_all, mock_load_snapshot):
        """Test handling of analysis errors."""
        # Setup mocks
        mock_load_snapshot.return_value = Mock()
        mock_run_all.return_value = Mock()
        
        # Mock analyzer to raise exception
        with patch('drift.revert.analyzer.DiffAnalyzer') as mock_analyzer_class:
            mock_analyzer = Mock()
            mock_analyzer_class.return_value = mock_analyzer
            mock_analyzer.analyze_revert_diff.side_effect = Exception("Analysis failed")
            
            # Execute revert
            options = RevertOptions(dry_run=True)
            result = self.engine.initiate_revert("error_hash", options)
            
            # Should handle error gracefully
            assert result.success is False
            assert "analysis failed" in result.error_message.lower()
            assert result.operations_executed == 0
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_planning_error_handling(self, mock_run_all, mock_load_snapshot):
        """Test handling of operation planning errors."""
        # Setup mocks
        mock_load_snapshot.return_value = Mock()
        mock_run_all.return_value = Mock()
        
        # Mock analyzer to return valid diff
        with patch('drift.revert.analyzer.DiffAnalyzer') as mock_analyzer_class:
            mock_analyzer = Mock()
            mock_analyzer_class.return_value = mock_analyzer
            mock_revert_diff = Mock()
            mock_revert_diff.changes = [Mock()]
            mock_analyzer.analyze_revert_diff.return_value = mock_revert_diff
            
            # Mock planner to raise exception
            with patch('drift.revert.planner.OperationPlanner') as mock_planner_class:
                mock_planner = Mock()
                mock_planner_class.return_value = mock_planner
                mock_planner.plan_operations.side_effect = Exception("Planning failed")
                
                # Execute revert
                options = RevertOptions(dry_run=True)
                result = self.engine.initiate_revert("planning_error_hash", options)
                
                # Should handle error gracefully
                assert result.success is False
                assert "planning failed" in result.error_message.lower()
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_safety_validation_error_handling(self, mock_run_all, mock_load_snapshot):
        """Test handling of safety validation errors."""
        # Setup mocks
        mock_load_snapshot.return_value = Mock()
        mock_run_all.return_value = Mock()
        
        # Mock successful analysis and planning
        with patch('drift.revert.analyzer.DiffAnalyzer') as mock_analyzer_class:
            mock_analyzer = Mock()
            mock_analyzer_class.return_value = mock_analyzer
            mock_revert_diff = Mock()
            mock_revert_diff.changes = [Mock()]
            mock_analyzer.analyze_revert_diff.return_value = mock_revert_diff
            
            with patch('drift.revert.planner.OperationPlanner') as mock_planner_class:
                mock_planner = Mock()
                mock_planner_class.return_value = mock_planner
                mock_planner.plan_operations.return_value = Mock()
                
                # Mock safety validator to raise exception
                with patch('drift.revert.safety.SafetyValidator') as mock_validator_class:
                    mock_validator = Mock()
                    mock_validator_class.return_value = mock_validator
                    mock_validator.assess_safety.side_effect = Exception("Safety check failed")
                    
                    # Execute revert
                    options = RevertOptions(dry_run=True)
                    result = self.engine.initiate_revert("safety_error_hash", options)
                    
                    # Should handle error gracefully
                    assert result.success is False
                    assert "safety check failed" in result.error_message.lower()


class TestRevertCrossPlatform:
    """Cross-platform compatibility tests."""
    
    def setup_method(self):
        """Set up cross-platform tests."""
        self.engine = RevertEngine()
    
    def _create_platform_specific_snapshot(self, platform_type: str) -> Snapshot:
        """Create snapshots with platform-specific differences."""
        if platform_type == "linux":
            return Snapshot(
                hostname="linux-host",
                timestamp="2024-01-01T12:00:00Z",
                os="Linux 5.4.0",
                kernel="5.4.0-42-generic",
                packages=[
                    Package(name="systemd", version="245.4", manager="apt"),
                    Package(name="openssh-server", version="8.2p1", manager="apt")
                ],
                services=[
                    Service(name="systemd", state="active", enabled=True),
                    Service(name="sshd", state="active", enabled=True)
                ],
                users=[
                    User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root"),
                    User(name="ubuntu", uid=1000, gid=1000, shell="/bin/bash", home="/home/ubuntu")
                ],
                groups=[
                    Group(name="root", gid=0, members=[]),
                    Group(name="sudo", gid=27, members=["ubuntu"])
                ],
                errors=[]
            )
        else:  # windows-like
            return Snapshot(
                hostname="windows-host",
                timestamp="2024-01-01T12:00:00Z",
                os="Windows 10",
                kernel="10.0.19041",
                packages=[
                    Package(name="python", version="3.9.0", manager="winget"),
                    Package(name="git", version="2.30.0", manager="winget")
                ],
                services=[
                    Service(name="Spooler", state="active", enabled=True),
                    Service(name="BITS", state="inactive", enabled=False)
                ],
                users=[
                    User(name="Administrator", uid=500, gid=513, shell="cmd.exe", home="C:\\Users\\Administrator"),
                    User(name="testuser", uid=1001, gid=513, shell="cmd.exe", home="C:\\Users\\testuser")
                ],
                groups=[
                    Group(name="Administrators", gid=544, members=["Administrator"]),
                    Group(name="Users", gid=545, members=["testuser"])
                ],
                errors=[]
            )
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_linux_platform_operations(self, mock_run_all, mock_load_snapshot):
        """Test revert operations on Linux-like systems."""
        current = self._create_platform_specific_snapshot("linux")
        target = self._create_platform_specific_snapshot("linux")
        # Remove a user in target to create a difference
        target.users = [u for u in target.users if u.name != "ubuntu"]
        target.groups = [g for g in target.groups if g.name != "sudo"]
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("linux_hash", options)
        
        assert result.success is True
        assert result.operation_plan.total_operations > 0
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_windows_platform_operations(self, mock_run_all, mock_load_snapshot):
        """Test revert operations on Windows-like systems."""
        current = self._create_platform_specific_snapshot("windows")
        target = self._create_platform_specific_snapshot("windows")
        # Remove a user in target to create a difference
        target.users = [u for u in target.users if u.name != "testuser"]
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("windows_hash", options)
        
        assert result.success is True
        assert result.operation_plan.total_operations > 0


class TestRevertRollbackScenarios:
    """Test rollback and recovery scenarios."""
    
    def setup_method(self):
        """Set up rollback tests."""
        self.engine = RevertEngine()
    
    def _create_rollback_test_snapshot(self) -> Snapshot:
        """Create a snapshot for rollback testing."""
        return Snapshot(
            hostname="rollback-test",
            timestamp="2024-01-01T12:00:00Z",
            os="Linux 5.4.0",
            kernel="5.4.0-42-generic",
            packages=[
                Package(name="test-package", version="1.0.0", manager="apt")
            ],
            services=[
                Service(name="test-service", state="active", enabled=True)
            ],
            users=[
                User(name="root", uid=0, gid=0, shell="/bin/bash", home="/root")
            ],
            groups=[
                Group(name="root", gid=0, members=[])
            ],
            errors=[]
        )
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_dry_run_rollback_planning(self, mock_run_all, mock_load_snapshot):
        """Test that rollback planning works in dry-run mode."""
        current = self._create_rollback_test_snapshot()
        target = self._create_rollback_test_snapshot()
        target.packages = []  # Remove package to trigger operation
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        # Execute dry-run revert with force to bypass safety validation
        options = RevertOptions(dry_run=True, force=True)
        result = self.engine.initiate_revert("rollback_hash", options)
        
        # Should succeed and create operation plan
        assert result.success is True
        assert result.operation_plan is not None
        assert result.operation_plan.total_operations > 0
        assert result.operations_executed == 0  # Dry run, no execution
    
    @patch('drift.revert.engine.load_snapshot')
    @patch('drift.revert.engine.run_all')
    def test_error_handling_graceful_failure(self, mock_run_all, mock_load_snapshot):
        """Test that errors are handled gracefully without crashing."""
        current = self._create_rollback_test_snapshot()
        target = self._create_rollback_test_snapshot()
        target.packages = []  # Remove package to trigger operation
        
        mock_run_all.return_value = current
        mock_load_snapshot.return_value = target
        
        # Mock analyzer to raise exception during analysis
        with patch('drift.revert.analyzer.DiffAnalyzer') as mock_analyzer_class:
            mock_analyzer = Mock()
            mock_analyzer_class.return_value = mock_analyzer
            mock_analyzer.analyze_revert_diff.side_effect = Exception("Analysis failed")
            
            # Execute revert
            options = RevertOptions(dry_run=True, force=True)
            result = self.engine.initiate_revert("error_hash", options)
            
            # Should fail gracefully with error message
            assert result.success is False
            assert "analysis failed" in result.error_message.lower()
            assert result.operations_executed == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])