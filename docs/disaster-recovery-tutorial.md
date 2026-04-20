# Disaster Recovery Tutorial Using Drift Revert

This comprehensive tutorial demonstrates how to use drift revert for disaster recovery scenarios, from minor service outages to complete system failures.

## Table of Contents

- [Disaster Recovery Overview](#disaster-recovery-overview)
- [Preparation and Planning](#preparation-and-planning)
- [Recovery Scenarios](#recovery-scenarios)
- [Emergency Procedures](#emergency-procedures)
- [Automation and Monitoring](#automation-and-monitoring)
- [Testing and Validation](#testing-and-validation)
- [Best Practices](#best-practices)

## Disaster Recovery Overview

### What is Disaster Recovery with Drift?

Drift revert enables rapid system recovery by:
- **State Restoration**: Quickly restore system to known good configurations
- **Selective Recovery**: Recover specific components without full system rebuild
- **Automated Rollback**: Implement automatic recovery triggers
- **Audit Trail**: Maintain complete recovery history for compliance

### Recovery Time Objectives (RTO) with Drift

| Scenario | Traditional Recovery | Drift Revert Recovery |
|----------|---------------------|----------------------|
| **Service Configuration** | 30-60 minutes | 2-5 minutes |
| **Package Rollback** | 15-45 minutes | 3-10 minutes |
| **User Account Issues** | 10-30 minutes | 1-3 minutes |
| **Complete System State** | 2-8 hours | 10-30 minutes |

## Preparation and Planning

### Step 1: Establish Baseline Snapshots

Create regular snapshots to establish recovery points:

```bash
# Create initial baseline
drift snapshot --message "Initial production baseline - $(date)"

# Set up automated snapshots
cat > /etc/cron.d/drift-snapshots << 'EOF'
# Take snapshot every 4 hours
0 */4 * * * root /usr/local/bin/drift snapshot --message "Scheduled snapshot - $(date)"

# Take snapshot before maintenance windows
0 1 * * 0 root /usr/local/bin/drift snapshot --message "Pre-maintenance snapshot - $(date)"
EOF
```

### Step 2: Create Recovery Point Classification

```bash
# Create recovery point tags
drift snapshot --message "GOLD: Post-deployment verification passed" --tag gold
drift snapshot --message "SILVER: Daily backup point" --tag silver  
drift snapshot --message "BRONZE: Hourly checkpoint" --tag bronze
```

### Step 3: Document Recovery Procedures

```bash
# Create recovery runbook
cat > ~/.drift/runbooks/disaster-recovery.md << 'EOF'
# Disaster Recovery Runbook

## Recovery Point Objectives (RPO)
- GOLD: Major deployments, weekly backups (max 1 week data loss)
- SILVER: Daily backups (max 24 hours data loss)  
- BRONZE: Hourly backups (max 4 hours data loss)

## Recovery Contacts
- Primary: ops-team@company.com
- Secondary: engineering@company.com
- Emergency: +1-555-ON-CALL

## Recovery Decision Matrix
| Impact | Scope | Recovery Target | Approval Required |
|--------|-------|----------------|-------------------|
| LOW | Single service | BRONZE (4h) | Team Lead |
| MEDIUM | Multiple services | SILVER (24h) | Engineering Manager |
| HIGH | System-wide | GOLD (1 week) | CTO Approval |
| CRITICAL | Data loss risk | GOLD + Manual | CEO Approval |
EOF
```

## Recovery Scenarios

### Scenario 1: Service Outage Recovery

**Situation**: Critical service (nginx) is down due to configuration changes.

#### Step 1: Assess the Situation

```bash
# Check current system status
drift status --health-check

# Identify what changed recently
drift diff HEAD~1
drift diff HEAD~2
```

**Example Output**:
```
🔍 Recent Changes (HEAD~1):
[SERVICE]
+ nginx  active → failed
+ php-fpm  active → inactive

[PACKAGE]  
+ nginx-extras  1.18.0 → 1.22.1

[PORT]
- 80/tcp  nginx (closed)
- 443/tcp  nginx (closed)
```

#### Step 2: Quick Service Recovery

```bash
# Immediate service restoration (fastest recovery)
drift revert to HEAD~1 --categories services --force

# Verify service recovery
systemctl status nginx
curl -I http://localhost
```

**Expected Result**:
```bash
$ drift revert to HEAD~1 --categories services --force
🚀 Executing emergency service recovery...

Batch 1/1: Processing 2 operations
  ✅ [SERVICE] Start nginx service (1.2s)
  ✅ [SERVICE] Start php-fpm service (0.8s)

✅ Service recovery completed (2.1s)
   Services restored to previous state
   
$ systemctl status nginx
● nginx.service - A high performance web server
   Active: active (running) since Mon 2024-01-15 14:32:15 UTC
```

#### Step 3: Full Recovery (if needed)

```bash
# If service recovery isn't sufficient, full revert
drift revert to HEAD~1 --dry-run  # Preview full revert
drift revert to HEAD~1            # Execute if safe
```

### Scenario 2: Database Service Recovery

**Situation**: Database service is corrupted after package update.

#### Step 1: Emergency Assessment

```bash
# Check database status
systemctl status postgresql
sudo -u postgres psql -c "SELECT version();" 2>&1

# Check recent changes
drift diff HEAD~1 | grep -E "(postgresql|database)"
```

#### Step 2: Database-Specific Recovery

```bash
# Stop database service first (safety)
systemctl stop postgresql

# Revert database-related changes only
drift revert to HEAD~1 --include-items "packages:postgresql*,services:postgresql,users:postgres" --dry-run
```

**Example Recovery Plan**:
```
📋 DATABASE RECOVERY PLAN:

Batch 1: Stop dependent services
  🔄 [SERVICE] Stop postgresql service

Batch 2: Revert database packages  
  🔄 [PACKAGE] Downgrade postgresql-14 → postgresql-12
  🔄 [PACKAGE] Downgrade postgresql-client-14 → postgresql-client-12

Batch 3: Restore database service
  🔄 [SERVICE] Start postgresql service
  🔄 [USER] Restore postgres user permissions

⚠️  Database Recovery Warnings:
  • Data compatibility between versions
  • Backup database before proceeding
  • Verify data integrity after recovery
```

#### Step 3: Execute Database Recovery

```bash
# Create emergency database backup
sudo -u postgres pg_dumpall > /tmp/emergency-backup-$(date +%Y%m%d-%H%M%S).sql

# Execute recovery
drift revert to HEAD~1 --include-items "packages:postgresql*,services:postgresql,users:postgres"

# Verify database recovery
sudo -u postgres psql -c "SELECT version();"
sudo -u postgres psql -c "SELECT count(*) FROM information_schema.tables;"
```

### Scenario 3: Complete System Recovery

**Situation**: Multiple system components are failing after a complex deployment.

#### Step 1: Comprehensive Assessment

```bash
# Get full system health report
drift status --comprehensive-check > /tmp/system-health-$(date +%Y%m%d-%H%M%S).log

# Analyze scope of changes
drift diff HEAD~3 --summary
```

**Example Assessment**:
```
🔍 COMPREHENSIVE SYSTEM ASSESSMENT:

Changes across 3 snapshots:
├─ 15 package changes (including kernel modules)
├─ 8 service changes (web, database, monitoring)
├─ 4 user account changes
├─ 12 configuration file changes
└─ 3 network interface changes

Risk Assessment: CRITICAL
├─ Kernel module changes detected
├─ Core service dependencies affected  
├─ Network configuration modified
└─ User permission changes

Recommended Recovery: Staged rollback to GOLD recovery point
```

#### Step 2: Staged Recovery Strategy

```bash
# Stage 1: Critical services first
drift revert to HEAD~3 --categories services --force
sleep 30  # Allow services to stabilize

# Stage 2: Network and connectivity
drift revert to HEAD~3 --categories ports,network --force  
sleep 30

# Stage 3: Packages (excluding kernel)
drift revert to HEAD~3 --categories packages --exclude-items "packages:linux-*" --force
sleep 60

# Stage 4: Users and permissions
drift revert to HEAD~3 --categories users,groups --force

# Stage 5: Remaining changes
drift revert to HEAD~3 --exclude services,ports,network,packages,users,groups
```

#### Step 3: Verification and Validation

```bash
# Comprehensive system verification
cat > /tmp/verify-recovery.sh << 'EOF'
#!/bin/bash

echo "🔍 System Recovery Verification"
echo "================================"

# Check critical services
CRITICAL_SERVICES="nginx postgresql redis-server"
for service in $CRITICAL_SERVICES; do
    if systemctl is-active --quiet $service; then
        echo "✅ $service: Running"
    else
        echo "❌ $service: Failed"
    fi
done

# Check network connectivity
if curl -s --max-time 5 http://localhost > /dev/null; then
    echo "✅ Web service: Responding"
else
    echo "❌ Web service: Not responding"
fi

# Check database connectivity
if sudo -u postgres psql -c "SELECT 1;" > /dev/null 2>&1; then
    echo "✅ Database: Connected"
else
    echo "❌ Database: Connection failed"
fi

# Check disk space
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ $DISK_USAGE -lt 90 ]; then
    echo "✅ Disk space: $DISK_USAGE% used"
else
    echo "⚠️  Disk space: $DISK_USAGE% used (high)"
fi

echo "================================"
echo "Recovery verification complete"
EOF

chmod +x /tmp/verify-recovery.sh
/tmp/verify-recovery.sh
```

## Emergency Procedures

### Emergency Procedure 1: System Unresponsive

**Situation**: System is unresponsive, SSH access limited.

#### Console Access Recovery

```bash
# If you have console access (IPMI, KVM, etc.)

# Boot into single-user mode or rescue mode
# Mount filesystems
mount -o remount,rw /

# Navigate to drift directory
cd ~/.drift

# Find last known good snapshot
ls -la snapshots/ | tail -5

# Execute emergency revert
drift revert to <last-good-snapshot> --emergency-mode --force
```

#### Network Boot Recovery

```bash
# If system won't boot, use network recovery

# Boot from network rescue image
# Mount system disk
mount /dev/sda1 /mnt

# Chroot into system
chroot /mnt

# Execute drift recovery
drift revert to HEAD~5 --emergency-mode --exclude packages
```

### Emergency Procedure 2: Data Corruption

**Situation**: File system corruption detected.

#### Safe Recovery Approach

```bash
# Step 1: Assess corruption scope
fsck -n /dev/sda1  # Read-only check

# Step 2: Create emergency backup
dd if=/dev/sda1 of=/backup/emergency-disk-image.dd bs=1M

# Step 3: Repair filesystem
fsck -y /dev/sda1

# Step 4: Mount and check drift integrity
mount /dev/sda1 /mnt
drift --data-dir /mnt/.drift status --integrity-check

# Step 5: Selective recovery
drift --data-dir /mnt/.drift revert to HEAD~1 --verify-integrity
```

### Emergency Procedure 3: Security Breach

**Situation**: Security compromise detected, need immediate lockdown and recovery.

#### Immediate Response

```bash
# Step 1: Immediate lockdown
# Disable network interfaces
ip link set eth0 down

# Stop all non-essential services
systemctl stop apache2 nginx ssh

# Step 2: Assess compromise scope
drift diff HEAD~5 | grep -E "(USER|GROUP|PACKAGE|SERVICE)"

# Step 3: Security-focused recovery
# Revert user accounts to known good state
drift revert to HEAD~5 --categories users,groups --force

# Revert packages to remove potentially compromised software
drift revert to HEAD~5 --categories packages --force

# Step 4: Re-enable with monitoring
systemctl start ssh  # Restore admin access
ip link set eth0 up  # Restore network

# Step 5: Full security audit
drift revert history --since "24 hours ago" > /tmp/security-audit.log
```

## Automation and Monitoring

### Automated Recovery Triggers

#### Health Check Based Recovery

```bash
# Create health monitoring script
cat > ~/.drift/scripts/health-monitor.sh << 'EOF'
#!/bin/bash

HEALTH_CHECK_URL="http://localhost/health"
MAX_FAILURES=3
FAILURE_COUNT=0
RECOVERY_SNAPSHOT=""

# Get last known good snapshot
get_recovery_snapshot() {
    # Find snapshot from when health was good (within last 24h)
    RECOVERY_SNAPSHOT=$(drift log --since "24h" --format json | jq -r '.[0].hash')
}

# Perform health check
health_check() {
    if curl -f -s --max-time 10 "$HEALTH_CHECK_URL" > /dev/null; then
        return 0
    else
        return 1
    fi
}

# Execute recovery
execute_recovery() {
    echo "🚨 Health check failed $MAX_FAILURES times, initiating recovery..."
    
    get_recovery_snapshot
    
    if [ -n "$RECOVERY_SNAPSHOT" ]; then
        drift revert to "$RECOVERY_SNAPSHOT" --categories services --force
        
        # Wait and recheck
        sleep 30
        if health_check; then
            echo "✅ Recovery successful"
            # Send success notification
            curl -X POST "$SLACK_WEBHOOK" -d '{"text":"🎉 Automatic recovery successful"}'
        else
            echo "❌ Recovery failed, escalating..."
            # Send alert
            curl -X POST "$PAGER_DUTY_WEBHOOK" -d '{"incident_key":"auto-recovery-failed"}'
        fi
    fi
}

# Main monitoring loop
while true; do
    if health_check; then
        FAILURE_COUNT=0
        echo "✅ Health check passed"
    else
        FAILURE_COUNT=$((FAILURE_COUNT + 1))
        echo "❌ Health check failed ($FAILURE_COUNT/$MAX_FAILURES)"
        
        if [ $FAILURE_COUNT -ge $MAX_FAILURES ]; then
            execute_recovery
            FAILURE_COUNT=0
        fi
    fi
    
    sleep 60  # Check every minute
done
EOF

chmod +x ~/.drift/scripts/health-monitor.sh

# Run as systemd service
cat > /etc/systemd/system/drift-health-monitor.service << 'EOF'
[Unit]
Description=Drift Health Monitor and Auto-Recovery
After=network.target

[Service]
Type=simple
User=root
ExecStart=/root/.drift/scripts/health-monitor.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl enable drift-health-monitor
systemctl start drift-health-monitor
```

#### Metric-Based Recovery

```bash
# Create metrics-based recovery
cat > ~/.drift/scripts/metrics-recovery.py << 'EOF'
#!/usr/bin/env python3

import requests
import subprocess
import time
import json
from datetime import datetime, timedelta

class MetricsRecovery:
    def __init__(self):
        self.prometheus_url = "http://localhost:9090"
        self.thresholds = {
            'error_rate': 0.10,      # 10% error rate
            'response_time_p95': 5.0, # 5 second 95th percentile
            'cpu_usage': 0.95,       # 95% CPU usage
            'memory_usage': 0.90,    # 90% memory usage
            'disk_usage': 0.95       # 95% disk usage
        }
        self.recovery_actions = {
            'error_rate': 'services',
            'response_time_p95': 'services',
            'cpu_usage': 'packages,services',
            'memory_usage': 'packages,services',
            'disk_usage': 'packages'
        }
    
    def check_metrics(self):
        """Check all metrics against thresholds."""
        alerts = []
        
        queries = {
            'error_rate': 'rate(http_requests_total{status=~"5.."}[5m])',
            'response_time_p95': 'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))',
            'cpu_usage': '1 - avg(rate(cpu_seconds_total{mode="idle"}[5m]))',
            'memory_usage': '1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
            'disk_usage': '1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'
        }
        
        for metric, query in queries.items():
            try:
                response = requests.get(f"{self.prometheus_url}/api/v1/query", 
                                      params={'query': query}, timeout=10)
                data = response.json()
                
                if data['data']['result']:
                    value = float(data['data']['result'][0]['value'][1])
                    threshold = self.thresholds[metric]
                    
                    if value > threshold:
                        alerts.append({
                            'metric': metric,
                            'value': value,
                            'threshold': threshold,
                            'recovery_action': self.recovery_actions[metric]
                        })
            except Exception as e:
                print(f"Error checking {metric}: {e}")
        
        return alerts
    
    def execute_recovery(self, alerts):
        """Execute recovery based on alerts."""
        
        # Determine recovery scope
        recovery_categories = set()
        for alert in alerts:
            categories = alert['recovery_action'].split(',')
            recovery_categories.update(categories)
        
        recovery_scope = ','.join(recovery_categories)
        
        print(f"🚨 Executing recovery for categories: {recovery_scope}")
        
        # Find recovery snapshot (last 2 hours)
        result = subprocess.run([
            'drift', 'log', '--since', '2h', '--format', 'json'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            snapshots = json.loads(result.stdout)
            if snapshots:
                target = snapshots[-1]['hash']
                
                # Execute recovery
                recovery_cmd = [
                    'drift', 'revert', 'to', target,
                    '--categories', recovery_scope,
                    '--force'
                ]
                
                recovery_result = subprocess.run(recovery_cmd, 
                                               capture_output=True, text=True)
                
                if recovery_result.returncode == 0:
                    print("✅ Metrics-based recovery completed successfully")
                    return True
                else:
                    print(f"❌ Recovery failed: {recovery_result.stderr}")
        
        return False

def main():
    recovery = MetricsRecovery()
    
    while True:
        try:
            alerts = recovery.check_metrics()
            
            if alerts:
                print(f"🚨 {len(alerts)} metric alerts detected:")
                for alert in alerts:
                    print(f"  • {alert['metric']}: {alert['value']:.3f} > {alert['threshold']}")
                
                if recovery.execute_recovery(alerts):
                    # Wait longer after successful recovery
                    time.sleep(300)  # 5 minutes
                else:
                    # Wait shorter after failed recovery
                    time.sleep(120)  # 2 minutes
            else:
                # Normal monitoring interval
                time.sleep(60)  # 1 minute
                
        except KeyboardInterrupt:
            print("Monitoring stopped")
            break
        except Exception as e:
            print(f"Error in monitoring loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
EOF

chmod +x ~/.drift/scripts/metrics-recovery.py

# Run metrics recovery
~/.drift/scripts/metrics-recovery.py &
```

## Testing and Validation

### Recovery Testing Framework

```bash
# Create recovery testing framework
cat > ~/.drift/testing/recovery-test.sh << 'EOF'
#!/bin/bash

# Recovery Testing Framework

TEST_SCENARIOS=(
    "service-failure:nginx"
    "package-corruption:postgresql"
    "user-compromise:www-data"
    "network-failure:eth0"
    "disk-full:/var"
)

run_test_scenario() {
    local scenario=$1
    local component=$2
    
    echo "🧪 Testing recovery scenario: $scenario ($component)"
    
    # Create test snapshot
    TEST_SNAPSHOT=$(drift snapshot --message "Pre-test snapshot for $scenario")
    
    case $scenario in
        "service-failure")
            # Simulate service failure
            systemctl stop $component
            systemctl disable $component
            ;;
        "package-corruption")
            # Simulate package issues
            apt remove -y $component
            ;;
        "user-compromise")
            # Simulate user account compromise
            usermod -s /bin/false $component
            ;;
        "network-failure")
            # Simulate network issues
            ip link set $component down
            ;;
        "disk-full")
            # Simulate disk space issues
            dd if=/dev/zero of=$component/test-fill bs=1M count=100
            ;;
    esac
    
    # Wait for issue to manifest
    sleep 10
    
    # Test recovery
    echo "🔄 Testing recovery..."
    START_TIME=$(date +%s)
    
    drift revert to $TEST_SNAPSHOT --force
    
    END_TIME=$(date +%s)
    RECOVERY_TIME=$((END_TIME - START_TIME))
    
    # Verify recovery
    case $scenario in
        "service-failure")
            if systemctl is-active --quiet $component; then
                echo "✅ Service recovery successful ($RECOVERY_TIME seconds)"
            else
                echo "❌ Service recovery failed"
            fi
            ;;
        "package-corruption")
            if dpkg -l | grep -q $component; then
                echo "✅ Package recovery successful ($RECOVERY_TIME seconds)"
            else
                echo "❌ Package recovery failed"
            fi
            ;;
        # Add other verification cases...
    esac
    
    echo "Recovery time: $RECOVERY_TIME seconds"
    echo "----------------------------------------"
}

# Run all test scenarios
for scenario_def in "${TEST_SCENARIOS[@]}"; do
    IFS=':' read -r scenario component <<< "$scenario_def"
    run_test_scenario "$scenario" "$component"
    sleep 30  # Cool-down between tests
done

echo "🎯 Recovery testing completed"
EOF

chmod +x ~/.drift/testing/recovery-test.sh

# Run recovery tests
~/.drift/testing/recovery-test.sh
```

## Best Practices

### 1. Recovery Point Management

```bash
# Implement recovery point lifecycle
cat > ~/.drift/scripts/recovery-point-management.sh << 'EOF'
#!/bin/bash

# Recovery Point Lifecycle Management

# Promote snapshots based on validation
promote_recovery_point() {
    local snapshot_hash=$1
    local level=$2  # bronze, silver, gold
    
    drift tag add $snapshot_hash $level
    echo "📈 Promoted $snapshot_hash to $level recovery point"
}

# Cleanup old recovery points
cleanup_recovery_points() {
    # Keep last 24 bronze points (hourly)
    drift log --tag bronze | tail -n +25 | while read hash; do
        drift tag remove $hash bronze
    done
    
    # Keep last 7 silver points (daily)
    drift log --tag silver | tail -n +8 | while read hash; do
        drift tag remove $hash silver
    done
    
    # Keep last 4 gold points (weekly)
    drift log --tag gold | tail -n +5 | while read hash; do
        drift tag remove $hash gold
    done
}

# Validate recovery points
validate_recovery_point() {
    local snapshot_hash=$1
    
    # Test revert in isolated environment
    drift revert to $snapshot_hash --dry-run --validate-only
    
    if [ $? -eq 0 ]; then
        echo "✅ Recovery point $snapshot_hash validated"
        return 0
    else
        echo "❌ Recovery point $snapshot_hash validation failed"
        return 1
    fi
}
EOF
```

### 2. Documentation and Runbooks

```bash
# Generate recovery documentation
cat > ~/.drift/scripts/generate-recovery-docs.sh << 'EOF'
#!/bin/bash

# Generate Recovery Documentation

generate_recovery_runbook() {
    local output_file="recovery-runbook-$(date +%Y%m%d).md"
    
    cat > $output_file << 'DOC'
# Disaster Recovery Runbook

## Current System State
DOC
    
    # Add current system information
    echo "- Generated: $(date)" >> $output_file
    echo "- Hostname: $(hostname)" >> $output_file
    echo "- OS: $(lsb_release -d | cut -f2)" >> $output_file
    echo "" >> $output_file
    
    # Add recovery points
    echo "## Available Recovery Points" >> $output_file
    echo "" >> $output_file
    
    drift log --format table | head -20 >> $output_file
    
    # Add recovery procedures
    cat >> $output_file << 'DOC'

## Emergency Recovery Procedures

### Service Outage
```bash
drift revert to HEAD~1 --categories services --force
```

### Package Issues
```bash
drift revert to HEAD~1 --categories packages --force
```

### Complete System Recovery
```bash
drift revert to <gold-recovery-point> --staged-execution
```

## Contact Information
- Primary: ops-team@company.com
- Emergency: +1-555-ON-CALL
DOC
    
    echo "📚 Recovery runbook generated: $output_file"
}

generate_recovery_runbook
EOF
```

### 3. Monitoring and Alerting

```bash
# Set up recovery monitoring
cat > ~/.drift/monitoring/recovery-alerts.yaml << 'EOF'
# Prometheus alerting rules for drift recovery

groups:
- name: drift_recovery
  rules:
  - alert: DriftRecoveryFailed
    expr: drift_recovery_success == 0
    for: 0m
    labels:
      severity: critical
    annotations:
      summary: "Drift recovery operation failed"
      description: "Recovery operation {{ $labels.recovery_id }} failed"

  - alert: DriftRecoveryTimeHigh
    expr: drift_recovery_duration_seconds > 300
    for: 0m
    labels:
      severity: warning
    annotations:
      summary: "Drift recovery taking too long"
      description: "Recovery operation taking {{ $value }} seconds"

  - alert: DriftNoRecentSnapshots
    expr: time() - drift_last_snapshot_timestamp > 14400  # 4 hours
    for: 0m
    labels:
      severity: warning
    annotations:
      summary: "No recent drift snapshots"
      description: "Last snapshot was {{ $value }} seconds ago"
EOF
```

This disaster recovery tutorial provides comprehensive guidance for using drift revert in emergency situations, from simple service outages to complete system failures. The key is preparation, testing, and having clear procedures for different scenarios.