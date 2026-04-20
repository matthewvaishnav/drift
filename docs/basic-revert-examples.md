# Basic Drift Revert Usage Examples

This document provides practical examples of using the drift revert functionality for common system recovery scenarios.

## Table of Contents

- [Getting Started](#getting-started)
- [Basic Revert Operations](#basic-revert-operations)
- [Common Scenarios](#common-scenarios)
- [Safety Features](#safety-features)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Getting Started

### Prerequisites

Before using drift revert, ensure you have:

1. **Drift installed and configured**:
```bash
# Install drift
pip install drift-tracker

# Verify installation
drift --version
```

2. **At least two snapshots**:
```bash
# Take initial snapshot
drift snapshot

# Make some changes to your system
sudo apt install nginx

# Take another snapshot
drift snapshot

# View snapshot history
drift log
```

3. **Understanding of your system state**:
```bash
# Check current differences
drift diff

# See what's changed since last snapshot
drift diff HEAD~1
```

### Basic Syntax

The basic revert command syntax is:
```bash
drift revert to <target> [options]
```

Where `<target>` can be:
- `HEAD~1` - Previous snapshot
- `HEAD~2` - Two snapshots ago
- `abc123` - Specific snapshot hash
- `abc` - Abbreviated hash (minimum 3 characters)

## Basic Revert Operations

### Example 1: Preview a Revert (Dry Run)

**Scenario**: You want to see what would happen if you reverted to the previous snapshot.

```bash
# Preview revert without making changes
drift revert to HEAD~1 --dry-run
```

**Expected Output**:
```
⚠️  REVERT OPERATION PREVIEW
┌─────────────────── Operation Summary ───────────────────┐
│ Target Snapshot     abc123def456 (2024-01-15 14:30)     │
│ Total Operations    3                                    │
│ Estimated Duration  15s                                  │
│ Overall Risk Level  MEDIUM                               │
└──────────────────────────────────────────────────────────┘

📋 PLANNED OPERATIONS:

Batch 1 (3 operations):
  🔄 [PACKAGE] Remove nginx 1.18.0
     Command: apt remove -y nginx
     Risk: MEDIUM - Package removal may affect dependent services
     
  🔄 [SERVICE] Stop nginx service
     Command: systemctl stop nginx
     Risk: LOW - Service stop operation
     
  🔄 [PORT] Close port 80/tcp
     Command: (automatic - service stop will close port)
     Risk: LOW - Port will be closed by service stop

💡 This is a DRY RUN - no changes will be made to your system.
   Run without --dry-run to execute these operations.
```

### Example 2: Simple Revert

**Scenario**: You installed a package that's causing issues and want to revert.

```bash
# Check what changed
drift diff HEAD~1

# Revert to previous state
drift revert to HEAD~1
```

**Expected Output**:
```
🔍 Analyzing differences between snapshots...
📋 Planning revert operations...
🛡️  Performing safety validation...

⚠️  SAFETY REVIEW REQUIRED
┌─────────────────── Safety Assessment ───────────────────┐
│ Overall Risk Level  MEDIUM                               │
│ Operations Count    3                                    │
│ Requires Backup     No                                   │
└──────────────────────────────────────────────────────────┘

🚨 Identified Risks:
  • Package removal may affect dependent services
  • Service stop will interrupt active connections

Do you want to proceed? [y/N]: y

🚀 Executing revert operations...

Batch 1/1: Processing 3 operations
  ✅ [SERVICE] Stop nginx service (2.1s)
  ✅ [PACKAGE] Remove nginx 1.18.0 (8.3s)
  ✅ [PORT] Port 80/tcp closed automatically

✅ Revert completed successfully (12.4s)
   System restored to snapshot abc123def456
   
📊 Summary:
   • Operations executed: 3/3
   • Duration: 12.4 seconds
   • No errors encountered
```

### Example 3: Revert with Exclusions

**Scenario**: You want to revert most changes but keep certain categories unchanged.

```bash
# Revert everything except packages
drift revert to HEAD~1 --exclude packages

# Revert everything except services and users
drift revert to HEAD~1 --exclude services,users
```

**Expected Output**:
```
🔍 Analyzing differences (excluding: packages)...
📋 Planning revert operations...

⚠️  REVERT OPERATION PREVIEW
┌─────────────────── Operation Summary ───────────────────┐
│ Target Snapshot     abc123def456                         │
│ Total Operations    1 (2 excluded)                       │
│ Excluded Categories packages                             │
│ Estimated Duration  3s                                   │
│ Overall Risk Level  LOW                                  │
└──────────────────────────────────────────────────────────┘

📋 PLANNED OPERATIONS:

Batch 1 (1 operation):
  🔄 [SERVICE] Stop nginx service
     Command: systemctl stop nginx
     Risk: LOW - Service stop operation

⚠️  EXCLUDED OPERATIONS:
  • [PACKAGE] Remove nginx 1.18.0 (excluded by --exclude packages)
  • [PORT] Close port 80/tcp (depends on excluded package operation)

Do you want to proceed? [y/N]: y

✅ Revert completed successfully (2.8s)
   1 operation executed, 2 operations excluded
```

## Common Scenarios

### Scenario 1: Undoing Package Installation

**Problem**: You installed a package that broke your system.

```bash
# See what packages were installed
drift diff HEAD~1 | grep PACKAGE

# Preview the revert
drift revert to HEAD~1 --dry-run

# Execute the revert
drift revert to HEAD~1
```

**Real Example**:
```bash
$ drift diff HEAD~1
  [PACKAGE]
  + docker-ce  20.10.21 → 24.0.7
  + docker-compose  1.29.2 → 2.21.0

  [SERVICE]
  + docker  inactive → active

$ drift revert to HEAD~1 --dry-run
⚠️  REVERT OPERATION PREVIEW
Batch 1 (3 operations):
  🔄 [SERVICE] Stop docker service
  🔄 [PACKAGE] Downgrade docker-ce 24.0.7 → 20.10.21
  🔄 [PACKAGE] Downgrade docker-compose 2.21.0 → 1.29.2

$ drift revert to HEAD~1
✅ Revert completed successfully
   Docker reverted to previous version
```

### Scenario 2: Reverting Service Changes

**Problem**: A service configuration change is causing issues.

```bash
# Check service changes
drift diff HEAD~1 | grep SERVICE

# Revert only service changes
drift revert to HEAD~1 --exclude packages,users,ports
```

**Real Example**:
```bash
$ drift diff HEAD~1
  [SERVICE]
  + apache2  active → inactive
  + nginx    inactive → active

$ drift revert to HEAD~1 --exclude packages,users,ports
📋 PLANNED OPERATIONS:
Batch 1 (2 operations):
  🔄 [SERVICE] Start apache2 service
  🔄 [SERVICE] Stop nginx service

✅ Revert completed successfully
   Service states restored
```

### Scenario 3: Undoing User Management Changes

**Problem**: User account changes need to be reverted.

```bash
# Check user changes
drift diff HEAD~1 | grep USER

# Preview user revert (high risk)
drift revert to HEAD~1 --dry-run
```

**Real Example**:
```bash
$ drift diff HEAD~1
  [USER]
  + deploy  uid=1001 shell=/bin/bash (added)
  
  [GROUP]
  + deploy  gid=1001 members=deploy (added)

$ drift revert to HEAD~1 --dry-run
⚠️  HIGH RISK OPERATIONS DETECTED
🚨 Critical Operations:
  • [USER] Delete user 'deploy' - This will remove the user account permanently
  • [GROUP] Delete group 'deploy' - This will remove the group permanently

⚠️  These operations require explicit confirmation due to high risk.
```

### Scenario 4: Reverting to Specific Snapshot

**Problem**: You need to go back several changes to a known good state.

```bash
# View snapshot history
drift log

# Find the target snapshot
drift show abc123def

# Revert to specific snapshot
drift revert to abc123def --dry-run
drift revert to abc123def
```

**Real Example**:
```bash
$ drift log
abc123def  2024-01-15 14:30  Known good state before deployment
def456abc  2024-01-15 15:45  After package updates
789012ghi  2024-01-15 16:20  After service configuration
HEAD       2024-01-15 17:00  Current state (broken)

$ drift revert to abc123def
🔍 Analyzing differences (3 snapshots back)...
📋 Planning revert operations...

⚠️  LARGE REVERT OPERATION
┌─────────────────── Operation Summary ───────────────────┐
│ Target Snapshot     abc123def (3 snapshots back)        │
│ Total Operations    12                                   │
│ Estimated Duration  45s                                  │
│ Overall Risk Level  HIGH                                 │
└──────────────────────────────────────────────────────────┘

This will revert 3 snapshots worth of changes. Continue? [y/N]: y

✅ Revert completed successfully (42.3s)
   System restored to known good state
```

## Safety Features

### Automatic Risk Assessment

Drift automatically assesses the risk level of revert operations:

```bash
$ drift revert to HEAD~1 --dry-run
🛡️  SAFETY ASSESSMENT:

Risk Level: MEDIUM
├─ LOW RISK (2 operations)
│  ├─ Stop nginx service
│  └─ Close port 80/tcp
└─ MEDIUM RISK (1 operation)
   └─ Remove nginx package - May affect dependent services

💡 Safety Recommendations:
   • Consider excluding high-risk categories if not needed
   • Verify no critical services depend on nginx
   • Ensure you have recent backups
```

### Force Mode (Use with Caution)

For automated scripts or when you're certain about the revert:

```bash
# Skip safety confirmation (dangerous!)
drift revert to HEAD~1 --force

# Combine with dry-run for safe automation
drift revert to HEAD~1 --force --dry-run
```

**Warning**: `--force` skips all safety confirmations. Only use when you're absolutely certain about the revert operation.

### Monitoring Revert Progress

For long-running reverts, monitor progress:

```bash
# Start revert in background
drift revert to HEAD~5 &

# Check status
drift revert status

# View detailed progress
drift revert status --verbose
```

**Example Output**:
```bash
$ drift revert status
🔄 Active Revert Operation

Revert ID: rev_abc123
Target: def456ghi (HEAD~5)
Status: In Progress
Progress: 8/15 operations completed (53%)
Elapsed: 32s
Estimated Remaining: 28s

Current Operation:
  🔄 [PACKAGE] Installing postgresql-client 12.16
```

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: "Target snapshot not found"

```bash
$ drift revert to abc123
❌ Error: Target snapshot 'abc123' not found

# Solution: Check available snapshots
$ drift log
$ drift revert to <correct-hash>
```

#### Issue 2: "Operation failed during execution"

```bash
$ drift revert to HEAD~1
❌ Error: Operation failed - package removal failed

# Check what went wrong
$ drift revert status
$ drift log --revert-history

# Try with exclusions
$ drift revert to HEAD~1 --exclude packages
```

#### Issue 3: "Safety validation failed"

```bash
$ drift revert to HEAD~1
❌ Error: Safety validation failed - insufficient disk space

# Check system resources
$ df -h
$ free -h

# Free up space and retry
$ drift revert to HEAD~1
```

#### Issue 4: "Permission denied"

```bash
$ drift revert to HEAD~1
❌ Error: Permission denied for system operation

# Run with appropriate privileges
$ sudo drift revert to HEAD~1

# Or check if drift has proper permissions
$ drift status --check-permissions
```

### Recovery from Failed Reverts

If a revert fails partway through:

```bash
# Check revert status
drift revert status

# View what operations completed
drift revert history --last

# If needed, manually fix issues and retry
drift revert to HEAD~1 --exclude <problematic-category>
```

## Best Practices

### 1. Always Use Dry Run First

```bash
# Good practice
drift revert to HEAD~1 --dry-run  # Preview first
drift revert to HEAD~1            # Then execute

# Avoid
drift revert to HEAD~1            # Direct execution without preview
```

### 2. Understand Your Changes

```bash
# Check what changed before reverting
drift diff HEAD~1
drift blame HEAD~1  # See who made changes

# Then revert with understanding
drift revert to HEAD~1
```

### 3. Use Exclusions When Appropriate

```bash
# If you only need to revert services
drift revert to HEAD~1 --exclude packages,users,ports

# If you want to keep user changes
drift revert to HEAD~1 --exclude users,groups
```

### 4. Monitor Long Operations

```bash
# For large reverts, monitor progress
drift revert to HEAD~5 &
watch -n 5 'drift revert status'
```

### 5. Keep Recent Snapshots

```bash
# Take snapshot before major changes
drift snapshot --message "Before deployment"

# This gives you a clear revert target
drift revert to HEAD~1  # Back to "Before deployment"
```

### 6. Test in Safe Environment First

```bash
# In development/staging
drift revert to HEAD~1 --dry-run  # Test the revert plan

# In production
drift revert to HEAD~1 --dry-run  # Verify same plan
drift revert to HEAD~1            # Execute with confidence
```

### 7. Document Your Reverts

```bash
# Add context to your revert operations
drift revert to HEAD~1  # Will be logged automatically

# Check revert history later
drift revert history
```

These examples cover the most common revert scenarios you'll encounter. Start with dry runs, understand the risks, and use exclusions to fine-tune your revert operations for safe and effective system recovery.