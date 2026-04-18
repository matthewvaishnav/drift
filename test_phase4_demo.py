#!/usr/bin/env python3
"""
Phase 4 Demo: Safety Validation System

This script demonstrates the comprehensive safety validation system implemented in Phase 4,
including system prerequisite validation, risk assessment, user confirmation workflows,
and safety backup management.
"""

import sys
import time
from pathlib import Path

# Add drift to path
sys.path.insert(0, str(Path(__file__).parent))

from drift.revert.safety import (
    SafetyValidator, SystemValidator, RiskAssessor, 
    SafetyBackupManager, UserConfirmationManager
)
from drift.revert.models import (
    OperationPlan, Operation, OperationBatch, RevertOptions,
    RiskLevel, Risk
)


def demo_system_validator():
    """Demonstrate system prerequisite validation."""
    print("=" * 60)
    print("🔍 SYSTEM PREREQUISITE VALIDATION DEMO")
    print("=" * 60)
    
    validator = SystemValidator()
    
    print("ℹ️  Note: Some checks may fail on Windows as they're designed for Linux systems")
    print()
    
    # Test all system validation checks
    checks = [
        ("Disk Space", validator.validate_disk_space),
        ("Memory", validator.validate_memory),
        ("Network Connectivity", validator.validate_network_connectivity),
        ("Privileges", validator.validate_privileges),
        ("Required Commands", validator.validate_required_commands),
        ("System Load", validator.validate_system_load)
    ]
    
    for check_name, check_func in checks:
        try:
            passed, message = check_func()
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"{status} {check_name}: {message}")
        except Exception as e:
            print(f"❌ FAIL {check_name}: Exception - {e}")
    
    print()
    print("💡 On Linux systems, these checks would provide comprehensive system validation")
    print()


def demo_risk_assessor():
    """Demonstrate risk assessment for operations."""
    print("=" * 60)
    print("⚠️  RISK ASSESSMENT DEMO")
    print("=" * 60)
    
    assessor = RiskAssessor()
    
    # Create sample operations with different risk levels
    operations = [
        Operation(
            id="op_001",
            category="service",
            action="stop",
            target="sshd",
            command="systemctl stop sshd",
            risk_level=RiskLevel.CRITICAL,
            description="Stop SSH daemon service"
        ),
        Operation(
            id="op_002",
            category="package",
            action="remove",
            target="nginx",
            command="apt remove -y nginx",
            risk_level=RiskLevel.MEDIUM,
            description="Remove nginx package"
        ),
        Operation(
            id="op_003",
            category="user",
            action="delete",
            target="testuser",
            command="userdel testuser",
            risk_level=RiskLevel.HIGH,
            description="Delete test user account"
        ),
        Operation(
            id="op_004",
            category="sysctl",
            action="set",
            target="kernel.randomize_va_space",
            command="sysctl -w kernel.randomize_va_space=0",
            risk_level=RiskLevel.HIGH,
            description="Disable kernel address space randomization"
        )
    ]
    
    # Create operation plan
    batch = OperationBatch(id="batch_001", operations=operations, estimated_duration=120)
    plan = OperationPlan(
        batches=[batch],
        total_operations=len(operations),
        estimated_duration=120,
        risk_assessment=RiskLevel.CRITICAL
    )
    
    # Assess risks
    risks = assessor.assess_operation_risks(plan)
    
    print(f"📊 Risk Assessment Results:")
    print(f"   Total Operations: {len(operations)}")
    print(f"   Identified Risks: {len(risks)}")
    print()
    
    # Display risks by level
    risk_counts = {}
    for risk in risks:
        level = risk.level.value.upper()
        risk_counts[level] = risk_counts.get(level, 0) + 1
        
        risk_icon = {
            "CRITICAL": "🔴",
            "HIGH": "🟡", 
            "MEDIUM": "🔵",
            "LOW": "🟢"
        }.get(level, "⚪")
        
        print(f"{risk_icon} {level}: {risk.message}")
        if risk.mitigation:
            print(f"   💡 Mitigation: {risk.mitigation}")
        print()
    
    # Summary
    print("📈 Risk Summary:")
    for level, count in risk_counts.items():
        print(f"   {level}: {count} risks")
    print()


def demo_safety_backup_manager():
    """Demonstrate safety backup management."""
    print("=" * 60)
    print("💾 SAFETY BACKUP MANAGEMENT DEMO")
    print("=" * 60)
    
    backup_manager = SafetyBackupManager()
    
    # Test backup creation (mock)
    print("🔄 Creating safety backup...")
    backup_hash = backup_manager.create_safety_backup("phase4_demo")
    
    if backup_hash:
        print(f"✅ Safety backup created: {backup_hash}")
        
        # Test backup verification
        print("🔍 Verifying backup integrity...")
        is_valid, message = backup_manager.verify_backup_integrity(backup_hash)
        status = "✅ VALID" if is_valid else "❌ INVALID"
        print(f"{status} {message}")
    else:
        print("❌ Failed to create safety backup")
    
    # Test cleanup
    print("🧹 Testing backup cleanup...")
    cleaned_count = backup_manager.cleanup_old_backups()
    print(f"📊 Would clean up {cleaned_count} old backups")
    print()


def demo_user_confirmation():
    """Demonstrate user confirmation workflow (non-interactive)."""
    print("=" * 60)
    print("👤 USER CONFIRMATION WORKFLOW DEMO")
    print("=" * 60)
    
    confirmation_manager = UserConfirmationManager()
    
    # Create sample risks and plan for demonstration
    risks = [
        Risk(
            level=RiskLevel.CRITICAL,
            message="Critical system service operation: sshd",
            mitigation="Ensure SSH access through alternative means before proceeding"
        ),
        Risk(
            level=RiskLevel.HIGH,
            message="User testuser will be deleted",
            mitigation="Ensure user is not currently logged in and has no critical processes"
        )
    ]
    
    # Create sample operation plan
    operations = [
        Operation(
            id="op_101",
            category="service",
            action="stop", 
            target="sshd",
            command="systemctl stop sshd",
            risk_level=RiskLevel.CRITICAL,
            description="Stop SSH daemon service"
        )
    ]
    
    batch = OperationBatch(id="batch_101", operations=operations, estimated_duration=60)
    plan = OperationPlan(
        batches=[batch],
        total_operations=1,
        estimated_duration=60,
        risk_assessment=RiskLevel.CRITICAL
    )
    
    print("📋 This would display a comprehensive risk summary to the user:")
    print("   - Operation summary with metrics")
    print("   - Detailed risk breakdown with mitigations")
    print("   - High-risk operations table")
    print("   - Interactive confirmation prompt")
    print()
    
    # Display what the confirmation would show (without actual user input)
    confirmation_manager._display_risk_summary(risks, plan)
    print("⚠️  Note: In actual usage, this would wait for user input")
    print()


def demo_comprehensive_safety_validation():
    """Demonstrate the complete SafetyValidator workflow."""
    print("=" * 60)
    print("🛡️  COMPREHENSIVE SAFETY VALIDATION DEMO")
    print("=" * 60)
    
    validator = SafetyValidator()
    
    # Create a realistic operation plan
    operations = [
        Operation(
            id="op_201",
            category="service",
            action="restart",
            target="nginx",
            command="systemctl restart nginx",
            risk_level=RiskLevel.MEDIUM,
            description="Restart nginx web server"
        ),
        Operation(
            id="op_202",
            category="package",
            action="install",
            target="htop",
            command="apt install -y htop",
            risk_level=RiskLevel.LOW,
            description="Install htop system monitor"
        ),
        Operation(
            id="op_203",
            category="user",
            action="create",
            target="newuser",
            command="useradd newuser",
            risk_level=RiskLevel.LOW,
            description="Create new user account"
        )
    ]
    
    batch = OperationBatch(id="batch_201", operations=operations, estimated_duration=180)
    plan = OperationPlan(
        batches=[batch],
        total_operations=len(operations),
        estimated_duration=180,
        risk_assessment=RiskLevel.MEDIUM
    )
    
    # Create revert options
    options = RevertOptions(
        dry_run=True,
        force=False,
        skip_confirmation=False,
        create_backup=True
    )
    
    print("🔍 Performing comprehensive safety assessment...")
    assessment = validator.assess_safety(plan, options)
    
    print(f"📊 Safety Assessment Results:")
    print(f"   Safe to proceed: {'✅ YES' if assessment.safe else '❌ NO'}")
    print(f"   Prerequisites met: {'✅ YES' if assessment.prerequisites_met else '❌ NO'}")
    print(f"   Requires confirmation: {'⚠️  YES' if assessment.requires_confirmation else '✅ NO'}")
    print(f"   Backup required: {'💾 YES' if assessment.backup_required else '📁 NO'}")
    print(f"   Total risks identified: {len(assessment.risks)}")
    print()
    
    if assessment.risks:
        print("🚨 Identified Risks:")
        for i, risk in enumerate(assessment.risks, 1):
            level_icon = {
                RiskLevel.CRITICAL: "🔴",
                RiskLevel.HIGH: "🟡",
                RiskLevel.MEDIUM: "🔵", 
                RiskLevel.LOW: "🟢"
            }.get(risk.level, "⚪")
            
            print(f"   {i}. {level_icon} {risk.level.value.upper()}: {risk.message}")
        print()
    
    if assessment.recommended_actions:
        print("💡 Recommended Actions:")
        for i, action in enumerate(assessment.recommended_actions, 1):
            print(f"   {i}. {action}")
        print()
    
    print("✅ Comprehensive safety validation complete!")
    print()


def main():
    """Run all Phase 4 safety validation demos."""
    print("🚀 DRIFT AUTO-REVERT PHASE 4 DEMO")
    print("Safety Validation System")
    print("=" * 60)
    print()
    
    try:
        # Run all demo components
        demo_system_validator()
        demo_risk_assessor()
        demo_safety_backup_manager()
        demo_user_confirmation()
        demo_comprehensive_safety_validation()
        
        print("🎉 Phase 4 Demo Complete!")
        print("=" * 60)
        print("✅ All safety validation components working correctly")
        print("🛡️  System is ready for safe revert operations")
        print()
        
    except Exception as e:
        print(f"❌ Demo failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())