# Advanced Drift Revert Scenarios

This document covers advanced revert scenarios including partial reverts, complex dependency handling, and sophisticated recovery strategies.

## Table of Contents

- [Partial Reverts](#partial-reverts)
- [Complex Dependency Scenarios](#complex-dependency-scenarios)
- [Multi-Snapshot Reverts](#multi-snapshot-reverts)
- [Conditional Reverts](#conditional-reverts)
- [Emergency Recovery](#emergency-recovery)
- [Automated Revert Workflows](#automated-revert-workflows)
- [Custom Revert Strategies](#custom-revert-strategies)

## Partial Reverts

### Scenario 1: Selective Category Revert

**Use Case**: You deployed a complex application stack but only want to revert the database changes while keeping the application updates.

```bash
# View the full diff
drift diff HEAD~3
```

**Output**:
```
[PACKAGE]
+ postgresql     12.16 → 14.9
+ redis-server   6.0.16 → 7.0.12
+ nginx          1.18.0 → 1.22.1

[SERVICE]
+ postgresql     inactive → active
+ redis-server   inactive → active
+ nginx          active → active (restarted)

[USER]
+ appuser        uid=1001 shell=/bin/bash (added)
+ dbuser         uid=1002 shell=/bin/bash (added)

[PORT]
+ 5432/tcp       postgresql
+ 6379/tcp       redis-server
+ 80/tcp         nginx (updated config)
```

**Selective Revert Strategy**:
```bash
# Only revert database-related changes
drift revert to HEAD~3 --exclude packages,nginx,appuser --dry-run
```

**Expected Result**:
```
📋 PLANNED OPERATIONS (Database-only revert):

Batch 1 (3 operations):
  🔄 [SERVICE] Stop postgresql service
  🔄 [SERVICE] Stop redis-server service  
  🔄 [USER] Delete user 'dbuser'

Batch 2 (2 operations):
  🔄 [PORT] Close port 5432/tcp
  🔄 [PORT] Close port 6379/tcp

⚠️  EXCLUDED OPERATIONS:
  • [PACKAGE] Downgrade postgresql (excluded)
  • [PACKAGE] Downgrade redis-server (excluded)  
  • [PACKAGE] Downgrade nginx (excluded)
  • [USER] Delete user 'appuser' (excluded)
```

### Scenario 2: Service-Only Revert

**Use Case**: Keep all packages and users but revert service states to handle a configuration issue.

```bash
# Revert only service states
drift revert to HEAD~2 --exclude packages,users,groups,ports --dry-run
```

**Advanced Filtering**:
```bash
# Create a custom revert profile
cat > ~/.drift/revert-profiles/services-only.json << EOF
{
  "name": "services-only",
  "description": "Revert only service states",
  "exclude_categories": ["packages", "users", "groups", "ports", "cron"],
  "include_categories": ["services"],
  "safety_overrides": {
    "require_confirmation": false,
    "max_risk_level": "medium"
  }
}
EOF

# Use the profile
drift revert to HEAD~2 --profile services-only
```

### Scenario 3: Targeted Item Revert

**Use Case**: Revert specific items within categories rather than entire categories.

```bash
# Revert only specific packages
drift revert to HEAD~1 --include-items "packages:nginx,packages:apache2"

# Revert specific services
drift revert to HEAD~1 --include-items "services:nginx,services:postgresql"

# Combine with exclusions
drift revert to HEAD~1 --exclude users --include-items "packages:nginx"
```

## Complex Dependency Scenarios

### Scenario 4: Handling Circular Dependencies

**Problem**: Package A depends on Package B, but Package B was updated to depend on the new version of Package A.

```bash
# View dependency analysis
drift revert to HEAD~1 --analyze-dependencies
```

**Output**:
```
🔍 DEPENDENCY ANALYSIS:

Detected Circular Dependencies:
┌─────────────────────────────────────────────────────────┐
│ Package libssl1.1 → Package openssl-dev → Package      │
│ libssl1.1 (circular)                                   │
└─────────────────────────────────────────────────────────┘

🛠️  RESOLUTION STRATEGY:
1. Break circular dependency by removing both packages
2. Reinstall packages in correct order
3. Verify dependency satisfaction

Execution Plan:
Batch 1: Remove packages with circular dependencies
Batch 2: Reinstall packages in dependency order
```

**Manual Resolution**:
```bash
# Use dependency breaking strategy
drift revert to HEAD~1 --break-circular-deps --dry-run

# Or handle manually with multiple steps
drift revert to HEAD~1 --exclude packages --dry-run  # Services first
drift revert to HEAD~1 --exclude services --force-dependency-order
```

### Scenario 5: Cross-Category Dependencies

**Use Case**: A service depends on a user account, which depends on a package installation.

```bash
# View cross-category dependencies
drift diff HEAD~1 --show-dependencies
```

**Output**:
```
[PACKAGE]
+ docker-ce  20.10.21 → 24.0.7
  └─ Required by: [USER] docker group membership
  └─ Required by: [SERVICE] docker service

[USER]  
+ alice  groups=docker (added to docker group)
  └─ Depends on: [PACKAGE] docker-ce installation
  └─ Required by: [SERVICE] docker service (user permissions)

[SERVICE]
+ docker  inactive → active
  └─ Depends on: [PACKAGE] docker-ce
  └─ Depends on: [USER] docker group setup
```

**Smart Revert with Dependencies**:
```bash
# Automatic dependency resolution
drift revert to HEAD~1 --resolve-dependencies --dry-run
```

**Expected Execution Order**:
```
📋 DEPENDENCY-RESOLVED EXECUTION PLAN:

Batch 1: Stop dependent services
  🔄 [SERVICE] Stop docker service

Batch 2: Remove user permissions  
  🔄 [USER] Remove alice from docker group

Batch 3: Remove packages
  🔄 [PACKAGE] Downgrade docker-ce 24.0.7 → 20.10.21

Dependencies automatically resolved in reverse order.
```

## Multi-Snapshot Reverts

### Scenario 6: Rolling Back Through Multiple Deployments

**Use Case**: You need to roll back through several deployments to reach a stable state.

```bash
# View deployment history
drift log --format=deployment
```

**Output**:
```
HEAD       2024-01-15 17:00  Deployment v3.2.1 (broken)
abc123def  2024-01-15 15:30  Deployment v3.2.0 (unstable)  
def456ghi  2024-01-15 14:00  Deployment v3.1.5 (stable)
ghi789jkl  2024-01-15 12:00  Deployment v3.1.4 (stable)
```

**Multi-Step Revert Strategy**:
```bash
# Analyze the full revert scope
drift revert to def456ghi --analyze-scope --dry-run
```

**Output**:
```
🔍 MULTI-SNAPSHOT REVERT ANALYSIS:

Reverting through 2 snapshots:
├─ HEAD → abc123def (Deployment v3.2.0)
│  ├─ 5 package changes
│  ├─ 3 service changes  
│  └─ 2 user changes
└─ abc123def → def456ghi (Deployment v3.1.5)
   ├─ 8 package changes
   ├─ 4 service changes
   └─ 1 configuration change

Total Impact:
├─ 13 package operations
├─ 7 service operations
├─ 2 user operations
└─ 1 configuration operation

Estimated Duration: 2m 45s
Risk Level: HIGH (due to scope)
```

**Staged Revert Approach**:
```bash
# Option 1: Single large revert
drift revert to def456ghi --staged-execution

# Option 2: Step-by-step revert
drift revert to abc123def  # First step back
drift revert status --wait  # Wait for completion
drift revert to def456ghi  # Second step back
```

### Scenario 7: Selective Multi-Snapshot Revert

**Use Case**: Revert specific categories across multiple snapshots while preserving others.

```bash
# Revert only package changes across 3 snapshots
drift revert to HEAD~3 --categories packages --dry-run

# Revert services and packages but keep user changes
drift revert to HEAD~3 --exclude users,groups,cron --dry-run
```

## Conditional Reverts

### Scenario 8: Health-Check Based Revert

**Use Case**: Only revert if certain conditions are met (service health, disk space, etc.).

```bash
# Create conditional revert script
cat > ~/.drift/scripts/conditional-revert.sh << 'EOF'
#!/bin/bash

# Health check function
check_system_health() {
    # Check disk space
    DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ $DISK_USAGE -gt 90 ]; then
        echo "ERROR: Disk usage too high ($DISK_USAGE%)"
        return 1
    fi
    
    # Check memory usage
    MEM_USAGE=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
    if [ $MEM_USAGE -gt 95 ]; then
        echo "ERROR: Memory usage too high ($MEM_USAGE%)"
        return 1
    fi
    
    # Check service health
    if ! systemctl is-active --quiet nginx; then
        echo "WARNING: nginx service not running"
    fi
    
    return 0
}

# Conditional revert logic
if check_system_health; then
    echo "System health OK, proceeding with revert..."
    drift revert to $1 --force
else
    echo "System health check failed, aborting revert"
    exit 1
fi
EOF

chmod +x ~/.drift/scripts/conditional-revert.sh

# Use conditional revert
~/.drift/scripts/conditional-revert.sh HEAD~1
```

### Scenario 9: Time-Based Revert Windows

**Use Case**: Only allow reverts during maintenance windows.

```bash
# Create maintenance window checker
cat > ~/.drift/scripts/maintenance-revert.sh << 'EOF'
#!/bin/bash

# Check if we're in maintenance window (2-4 AM)
HOUR=$(date +%H)
if [ $HOUR -lt 2 ] || [ $HOUR -gt 4 ]; then
    echo "ERROR: Reverts only allowed during maintenance window (2-4 AM)"
    echo "Current time: $(date)"
    exit 1
fi

# Check if it's a weekday (no weekend reverts)
DAY=$(date +%u)
if [ $DAY -gt 5 ]; then
    echo "ERROR: No reverts allowed on weekends"
    exit 1
fi

echo "Maintenance window active, proceeding with revert..."
drift revert to $1 "${@:2}"
EOF

chmod +x ~/.drift/scripts/maintenance-revert.sh

# Use maintenance window revert
~/.drift/scripts/maintenance-revert.sh HEAD~1 --dry-run
```

## Emergency Recovery

### Scenario 10: System in Broken State

**Use Case**: System is partially broken and normal revert might fail.

```bash
# Emergency revert with maximum safety
drift revert to HEAD~1 --emergency-mode --dry-run
```

**Emergency Mode Features**:
```
🚨 EMERGENCY REVERT MODE ACTIVATED

Safety Overrides:
├─ Skip non-critical validations
├─ Use minimal resource requirements  
├─ Enable aggressive error recovery
├─ Create automatic safety backup
└─ Use simplified execution strategy

⚠️  Emergency mode bypasses some safety checks.
    Only use when system is in critical state.
```

**Step-by-Step Emergency Recovery**:
```bash
# Step 1: Assess system state
drift status --emergency-check

# Step 2: Create emergency backup
drift snapshot --emergency-backup

# Step 3: Minimal revert (services only)
drift revert to HEAD~1 --emergency-mode --exclude packages,users

# Step 4: Verify system stability
drift status --health-check

# Step 5: Complete revert if stable
drift revert to HEAD~1 --exclude services  # Packages and users
```

### Scenario 11: Network Connectivity Issues

**Use Case**: Revert when network-dependent operations might fail.

```bash
# Revert with network isolation
drift revert to HEAD~1 --offline-mode --dry-run
```

**Offline Mode Strategy**:
```
🌐 OFFLINE REVERT MODE

Network-dependent operations will be skipped:
├─ Package downloads (use local cache only)
├─ Repository updates (skip)
├─ External service checks (skip)
└─ Remote dependency resolution (use local data)

⚠️  Some operations may fail without network access.
    Verify local package cache is available.
```

## Automated Revert Workflows

### Scenario 12: CI/CD Integration

**Use Case**: Automatic revert on deployment failure.

```bash
# Create deployment revert hook
cat > ~/.drift/hooks/post-deploy-check.sh << 'EOF'
#!/bin/bash

DEPLOYMENT_SNAPSHOT=$1
HEALTH_CHECK_URL=$2

# Wait for deployment to settle
sleep 30

# Run health checks
if curl -f -s "$HEALTH_CHECK_URL" > /dev/null; then
    echo "✅ Deployment health check passed"
    exit 0
else
    echo "❌ Deployment health check failed"
    echo "🔄 Initiating automatic revert..."
    
    # Automatic revert to pre-deployment state
    drift revert to "$DEPLOYMENT_SNAPSHOT" --force --exclude users
    
    if [ $? -eq 0 ]; then
        echo "✅ Automatic revert completed successfully"
        # Notify team
        curl -X POST "$SLACK_WEBHOOK" -d "{\"text\":\"🚨 Deployment failed and was automatically reverted\"}"
    else
        echo "❌ Automatic revert failed - manual intervention required"
        # Alert on-call
        curl -X POST "$PAGER_DUTY_WEBHOOK" -d "{\"incident_key\":\"deploy-revert-failed\"}"
    fi
fi
EOF

# Use in deployment pipeline
PRE_DEPLOY_SNAPSHOT=$(drift snapshot --message "Pre-deployment backup")
# ... run deployment ...
~/.drift/hooks/post-deploy-check.sh "$PRE_DEPLOY_SNAPSHOT" "http://localhost/health"
```

### Scenario 13: Monitoring-Triggered Reverts

**Use Case**: Automatic revert based on monitoring alerts.

```bash
# Create monitoring integration
cat > ~/.drift/scripts/monitoring-revert.py << 'EOF'
#!/usr/bin/env python3

import requests
import subprocess
import json
import time
from datetime import datetime, timedelta

class MonitoringRevert:
    def __init__(self):
        self.metrics_url = "http://prometheus:9090/api/v1/query"
        self.revert_threshold = {
            'error_rate': 0.05,  # 5% error rate
            'response_time': 2.0,  # 2 second response time
            'cpu_usage': 0.90     # 90% CPU usage
        }
    
    def check_metrics(self):
        """Check if metrics exceed thresholds."""
        
        queries = {
            'error_rate': 'rate(http_requests_total{status=~"5.."}[5m])',
            'response_time': 'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))',
            'cpu_usage': 'avg(cpu_usage_percent)'
        }
        
        alerts = []
        
        for metric, query in queries.items():
            response = requests.get(self.metrics_url, params={'query': query})
            data = response.json()
            
            if data['data']['result']:
                value = float(data['data']['result'][0]['value'][1])
                threshold = self.revert_threshold[metric]
                
                if value > threshold:
                    alerts.append({
                        'metric': metric,
                        'value': value,
                        'threshold': threshold
                    })
        
        return alerts
    
    def execute_revert(self, reason):
        """Execute automatic revert."""
        
        print(f"🚨 Executing automatic revert: {reason}")
        
        # Get last known good snapshot (within last 2 hours)
        result = subprocess.run([
            'drift', 'log', '--since', '2h', '--format', 'json'
        ], capture_output=True, text=True)
        
        snapshots = json.loads(result.stdout)
        
        if snapshots:
            target = snapshots[-1]['hash']  # Most recent snapshot
            
            # Execute revert
            revert_result = subprocess.run([
                'drift', 'revert', 'to', target, '--force', '--exclude', 'users'
            ], capture_output=True, text=True)
            
            if revert_result.returncode == 0:
                print("✅ Automatic revert completed successfully")
                return True
            else:
                print(f"❌ Automatic revert failed: {revert_result.stderr}")
                return False
        
        return False

def main():
    monitor = MonitoringRevert()
    
    while True:
        alerts = monitor.check_metrics()
        
        if alerts:
            reason = f"Metrics exceeded thresholds: {', '.join([f'{a['metric']}={a['value']:.3f}' for a in alerts])}"
            
            if monitor.execute_revert(reason):
                # Wait before checking again
                time.sleep(300)  # 5 minutes
            else:
                # Alert failed, wait longer
                time.sleep(600)  # 10 minutes
        else:
            # Normal check interval
            time.sleep(60)  # 1 minute

if __name__ == "__main__":
    main()
EOF

chmod +x ~/.drift/scripts/monitoring-revert.py

# Run monitoring daemon
~/.drift/scripts/monitoring-revert.py &
```

## Custom Revert Strategies

### Scenario 14: Blue-Green Deployment Revert

**Use Case**: Revert by switching traffic between blue and green environments.

```bash
# Create blue-green revert strategy
cat > ~/.drift/strategies/blue-green-revert.sh << 'EOF'
#!/bin/bash

CURRENT_ENV=$1
TARGET_SNAPSHOT=$2

if [ "$CURRENT_ENV" = "blue" ]; then
    NEW_ENV="green"
else
    NEW_ENV="blue"
fi

echo "🔄 Blue-Green Revert Strategy"
echo "Current: $CURRENT_ENV → Target: $NEW_ENV"

# Step 1: Prepare target environment
echo "📋 Preparing $NEW_ENV environment..."
drift revert to "$TARGET_SNAPSHOT" --environment "$NEW_ENV" --dry-run

# Step 2: Switch load balancer
echo "🔀 Switching traffic to $NEW_ENV..."
# Update load balancer configuration
nginx -s reload

# Step 3: Verify traffic switch
echo "✅ Traffic switched to $NEW_ENV environment"
echo "🔍 Monitor for 5 minutes before declaring success..."

# Step 4: Monitor new environment
sleep 300

echo "✅ Blue-Green revert completed successfully"
EOF

chmod +x ~/.drift/strategies/blue-green-revert.sh

# Use blue-green revert
~/.drift/strategies/blue-green-revert.sh blue HEAD~1
```

### Scenario 15: Canary Revert Strategy

**Use Case**: Gradually revert changes by rolling back a percentage of traffic.

```bash
# Create canary revert strategy
cat > ~/.drift/strategies/canary-revert.sh << 'EOF'
#!/bin/bash

TARGET_SNAPSHOT=$1
CANARY_PERCENTAGE=${2:-10}  # Default 10%

echo "🐤 Canary Revert Strategy"
echo "Target: $TARGET_SNAPSHOT"
echo "Canary Percentage: $CANARY_PERCENTAGE%"

# Step 1: Create canary environment with reverted state
echo "📋 Creating canary environment..."
drift revert to "$TARGET_SNAPSHOT" --environment canary --force

# Step 2: Route percentage of traffic to canary
echo "🔀 Routing $CANARY_PERCENTAGE% traffic to canary..."
# Update load balancer weights
# This would integrate with your load balancer (nginx, haproxy, etc.)

# Step 3: Monitor canary metrics
echo "📊 Monitoring canary metrics..."
for i in {1..12}; do  # Monitor for 12 minutes
    # Check error rates, response times, etc.
    ERROR_RATE=$(curl -s "http://prometheus:9090/api/v1/query?query=rate(http_requests_total{env=\"canary\",status=~\"5..\"}[1m])" | jq -r '.data.result[0].value[1] // 0')
    
    if (( $(echo "$ERROR_RATE > 0.01" | bc -l) )); then
        echo "❌ Canary showing high error rate: $ERROR_RATE"
        echo "🔄 Rolling back canary..."
        # Route traffic back to main environment
        exit 1
    fi
    
    echo "✅ Canary healthy (minute $i/12)"
    sleep 60
done

# Step 4: Gradually increase canary traffic
for PERCENTAGE in 25 50 75 100; do
    echo "🔀 Increasing canary traffic to $PERCENTAGE%..."
    # Update load balancer weights
    sleep 300  # Wait 5 minutes between increases
done

echo "✅ Canary revert completed successfully"
EOF

chmod +x ~/.drift/strategies/canary-revert.sh

# Use canary revert
~/.drift/strategies/canary-revert.sh HEAD~1 20  # 20% canary traffic
```

These advanced scenarios demonstrate the flexibility and power of the drift revert system for handling complex production environments and sophisticated deployment strategies.