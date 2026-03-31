# drift 🔍

> **git for your server state.**
> Snapshot, diff, and blame every change to packages, services, ports, users, cron jobs, and kernel parameters — automatically.

---

## The problem

Something broke on prod at 2am. What changed?

Right now your answer is: grep bash history, check deploy logs, ask in Slack, look at git commits, pray someone ran Ansible. Four different places, all incomplete, all manual.

**drift answers it in one command.**

```
$ drift diff HEAD~1
  [PACKAGE]
  + postgresql  14.8 → 15.2
  - libssl1.1   1.1.1  (removed)

  [SERVICE]
  + postgresql  inactive → active  ⚠
  + postgresql-15  added  ⚠

  [PORT]
  + 5432/tcp  postgres  ⚠

  [USER]
  + deploy  uid=1001 shell=/bin/bash  ⚠
```

---

## What it tracks

| Category | What's captured |
|----------|----------------|
| **Packages** | apt/dpkg, yum/rpm, pip, snap, npm (global), gem |
| **Services** | Every systemd unit — state (active/inactive/failed) + enabled status |
| **Open ports** | All listening TCP/UDP ports + process name |
| **Users** | /etc/passwd — uid, shell, home, group memberships |
| **Groups** | /etc/group — gid, members |
| **Cron jobs** | /etc/crontab, /etc/cron.d/*, all user crontabs |
| **Sysctl** | All kernel parameters (security-relevant ones flagged critical) |
| **Mounts** | /proc/mounts (virtual filesystems excluded) |
| **Environment** | /etc/environment, /etc/profile.d/*.sh |
| **Kernel modules** | /proc/modules |

---

## Install

```bash
# From PyPI:
pip install drift-tracker
# or (recommended for CLI tools):
pipx install drift-tracker

# Or from source:
git clone https://github.com/matthewvaishnav/drift
pip install ./drift
```

> Note: drift targets Linux (it reads `/proc`, `/etc`, and integrates with systemd/PAM).  
> On Windows, run it inside WSL or on a Linux host.

### Install as systemd daemon (recommended)

```bash
sudo drift install
```

This installs:
- A systemd service that takes a snapshot every hour
- A PAM hook that takes a snapshot on every SSH login/logout

---

## Commands

```bash
# Take a snapshot right now
drift snapshot

# Show the commit log (newest first)
drift log
drift log 50

# Show what changed since the last snapshot
drift diff

# Show changes between specific commits
drift diff HEAD~1         # last two snapshots
drift diff abc123         # specific commit vs HEAD
drift diff abc123 def456  # between two commits

# Show verbose diff (full before/after values)
drift diff -v

# Who caused these changes? (correlates with SSH logs)
drift blame              # HEAD
drift blame abc123       # specific commit

# Full details of a snapshot
drift show abc123
drift show abc123 --json  # raw JSON output

# Search across all snapshots
drift search nginx
drift search "port 4444"

# Current server state summary
drift status

# Storage stats
drift stats

# Manage the background daemon
drift daemon start
drift daemon stop
drift daemon status

# PAM hook setup instructions
drift init-pam

# Install systemd service + PAM hook (requires root)
sudo drift install
```

---

## How it works

```
                ┌─────────────────────┐
  SSH login ───►│                     │
  SSH logout──► │   drift daemon      │──► take snapshot
  Scheduled────►│   (hourly)          │
  drift snap───►│                     │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │   Collectors        │
                │  packages · services│
                │  ports · users      │
                │  cron · sysctl      │
                │  mounts · env       │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │  Content-Addressable│
                │  Object Store       │──► ~/.drift/objects/ab/cdef1234...
                │  (SHA-256, gzip)    │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │  Append-Only Log    │──► ~/.drift/log  (JSONL)
                │  (like git commits) │
                └─────────────────────┘
```

Storage is designed like git:
- Each snapshot is stored once, addressed by its SHA-256 hash
- The log is append-only — the audit trail cannot be silently altered
- Snapshots are gzip-compressed — a typical server state is 50–200 KB

---

## `drift blame` — who changed what

```
$ drift blame abc123

  drift blame  abc123
  Window: 2026-03-19 14:00 → 2026-03-19 15:32 on app01.prod.example.com

  SSH sessions during this window:
  ┌─────────┬────────────────┬─────────┬──────────────────────┐
  │ User    │ From           │ Action  │ Time                 │
  ├─────────┼────────────────┼─────────┼──────────────────────┤
  │ alice   │ 10.0.0.42      │ login   │ 2026-03-19 14:23     │
  └─────────┴────────────────┴─────────┴──────────────────────┘

  7 changes in this commit:
  + [package]  postgresql 15.2
  + [service]  postgresql  ⚠
  + [port]     5432/tcp  ⚠
  + [user]     deploy  ⚠
  ...
```

---

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DRIFT_DIR` | `~/.drift` | Storage directory |
| `DRIFT_INTERVAL` | `3600` | Snapshot interval in seconds |
| `DRIFT_EXCLUDE_COLLECTORS` | `` | Comma-separated collectors to skip |
| `DRIFT_SLACK_WEBHOOK` | `` | Slack webhook for critical change alerts |
| `DRIFT_LOG_FILE` | `` | Log file for the daemon |
| `DRIFT_DEBUG` | `` | Set to `1` for traceback on errors |

---

## Why not X?

| Tool | Why it's not drift |
|------|-------------------|
| **Tripwire / AIDE** | File integrity only. No packages, services, users, or ports. No history. |
| **driftctl** | Terraform-managed cloud resources only. Zero OS awareness. |
| **osquery** | Shows current state. No history, no diff, no blame. |
| **Ansible facts** | Point-in-time snapshot. No daemon, no history, no diff. |

drift is the only tool that does **git-style history for full server state**.

---

## Run tests

```bash
pip install pytest
python -m pytest tests/ -v
```

60 tests. All green.
