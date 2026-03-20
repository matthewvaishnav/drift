"""
drift/export.py

Converts a drift Snapshot into actionable IaC.
This is the bridge between drift (what IS) and replay (what TO DO).

Given a snapshot of a server's current state, generate:
  - An Ansible playbook that reproduces that state on a fresh server
  - An idempotent shell script
  - A requirements/manifest file (packages only)

Usage:
    drift export                        # export current HEAD snapshot
    drift export abc123                 # export specific snapshot
    drift export --format shell         # shell script instead of ansible
    drift export --format packages      # just package list
    drift export --out setup.yml        # save to file
"""
from __future__ import annotations
import json
from typing import Optional
from drift.models import Snapshot, Package, Service, Port, User, CronJob, SysctlParam, Mount


# ══════════════════════════════════════════════════════════════════════════════
# ANSIBLE PLAYBOOK GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def _pkg_manager_module(manager: str) -> str:
    return {
        "dpkg":  "ansible.builtin.apt",
        "apt":   "ansible.builtin.apt",
        "rpm":   "ansible.builtin.yum",
        "yum":   "ansible.builtin.yum",
        "dnf":   "ansible.builtin.dnf",
        "pip":   "ansible.builtin.pip",
        "snap":  "community.general.snap",
        "npm":   "community.general.npm",
        "gem":   "community.general.gem",
        "apk":   "community.general.apk",
        "pacman":"community.general.pacman",
    }.get(manager, "ansible.builtin.package")


def _tasks_from_snapshot(snap: Snapshot) -> list[dict]:
    tasks = []

    # ── packages grouped by manager ──────────────────────────────────────────
    by_manager: dict[str, list[Package]] = {}
    for pkg in snap.packages:
        by_manager.setdefault(pkg.manager, []).append(pkg)

    for manager, pkgs in sorted(by_manager.items()):
        module = _pkg_manager_module(manager)

        if manager in ("dpkg", "apt"):
            names = sorted(p.name for p in pkgs)
            tasks.append({
                "name": f"Install {len(names)} apt packages",
                module: {"name": names, "state": "present"},
            })

        elif manager in ("rpm", "yum", "dnf"):
            names = sorted(p.name for p in pkgs)
            tasks.append({
                "name": f"Install {len(names)} rpm packages",
                module: {"name": names, "state": "present"},
            })

        elif manager == "pip":
            # Group into one task with requirements-style list
            reqs = sorted(f"{p.name}=={p.version}" for p in pkgs)
            tasks.append({
                "name": f"Install {len(pkgs)} Python packages",
                "ansible.builtin.pip": {
                    "name": [p.name for p in sorted(pkgs, key=lambda x: x.name)],
                    "state": "present",
                },
            })

        elif manager == "snap":
            for pkg in sorted(pkgs, key=lambda x: x.name):
                tasks.append({
                    "name": f"Install snap: {pkg.name}",
                    "community.general.snap": {
                        "name": pkg.name,
                        "state": "present",
                    },
                })

        elif manager == "npm":
            for pkg in sorted(pkgs, key=lambda x: x.name):
                tasks.append({
                    "name": f"Install npm global: {pkg.name}",
                    "community.general.npm": {
                        "name": pkg.name,
                        "global": True,
                        "state": "present",
                    },
                })

    # ── users ─────────────────────────────────────────────────────────────────
    # Real users: uid >= 1000, uid < 60000 (excludes nobody=65534, nfsnobody=65534)
    _PSEUDO_USERS = {"nobody", "nfsnobody", "nogroup"}
    real_users = [
        u for u in snap.users
        if u.uid >= 1000
        and u.uid < 60000
        and u.name not in _PSEUDO_USERS
        and u.shell not in ("/bin/false", "/usr/sbin/nologin", "/sbin/nologin")
    ]
    for user in sorted(real_users, key=lambda x: x.uid):
        t: dict = {
            "name": f"Ensure user: {user.name}",
            "ansible.builtin.user": {
                "name":       user.name,
                "uid":        user.uid,
                "shell":      user.shell,
                "home":       user.home,
                "create_home": True,
                "state":      "present",
            },
        }
        if user.groups:
            non_primary = [g for g in user.groups if g != user.name]
            if non_primary:
                t["ansible.builtin.user"]["groups"] = sorted(non_primary)
                t["ansible.builtin.user"]["append"] = True
        tasks.append(t)

    # ── services ──────────────────────────────────────────────────────────────
    # Only services that are both active AND enabled
    running_enabled = [
        s for s in snap.services
        if s.state == "active" and s.enabled
        and not s.name.startswith("user@")      # skip per-user services
        and not s.name.startswith("session-")
        and not s.name.endswith("@")
    ]
    # Deduplicate by name
    seen_svcs: set[str] = set()
    for svc in sorted(running_enabled, key=lambda x: x.name):
        if svc.name in seen_svcs:
            continue
        seen_svcs.add(svc.name)
        tasks.append({
            "name": f"Ensure {svc.name} is running and enabled",
            "ansible.builtin.service": {
                "name":    svc.name,
                "state":   "started",
                "enabled": True,
            },
        })

    # ── sysctl (non-default values only — heuristic) ─────────────────────────
    _SYSCTL_NOTABLE = {
        "net.ipv4.ip_forward":                      "0",
        "net.ipv4.conf.all.accept_redirects":       "1",
        "net.ipv4.conf.all.send_redirects":         "1",
        "vm.swappiness":                            "60",
        "net.ipv4.tcp_syncookies":                  "1",
        "kernel.randomize_va_space":                "2",
    }
    for param in snap.sysctl:
        default = _SYSCTL_NOTABLE.get(param.key)
        if default is not None and param.value != default:
            tasks.append({
                "name": f"Set sysctl {param.key}",
                "ansible.posix.sysctl": {
                    "name":   param.key,
                    "value":  param.value,
                    "state":  "present",
                    "reload": True,
                },
            })

    # ── cron jobs (non-system) ────────────────────────────────────────────────
    for job in snap.cron_jobs:
        if job.source == "crontab" and job.owner not in ("root",):
            continue  # skip system crontab entries in user-level export
        tasks.append({
            "name": f"Cron job: {job.command[:40]}",
            "ansible.builtin.cron": {
                "name":    f"drift-export: {job.command[:40]}",
                "user":    job.owner,
                "job":     job.command,
                "minute":  job.schedule.split()[0] if " " in job.schedule else "*",
                "hour":    job.schedule.split()[1] if len(job.schedule.split()) > 1 else "*",
                "day":     job.schedule.split()[2] if len(job.schedule.split()) > 2 else "*",
                "month":   job.schedule.split()[3] if len(job.schedule.split()) > 3 else "*",
                "weekday": job.schedule.split()[4] if len(job.schedule.split()) > 4 else "*",
            },
        })

    return tasks


def to_ansible(snap: Snapshot) -> str:
    """Generate a complete Ansible playbook from a snapshot."""
    import yaml

    tasks = _tasks_from_snapshot(snap)
    timestamp = snap.timestamp[:16].replace("T", " ")

    playbook = [{
        "name": f"Reproduced state from drift snapshot ({snap.hostname} @ {timestamp})",
        "hosts": "all",
        "become": True,
        "gather_facts": True,
        "vars": {
            "# drift_source_host":   snap.hostname,
            "# drift_snapshot_time": snap.timestamp,
            "# drift_os":           snap.os,
        },
        "tasks": tasks,
    }]

    header = f"""\
---
# ╔══════════════════════════════════════════════════════════════════╗
# ║  drift export — reproduced server state as Ansible playbook    ║
# ║  Source host : {snap.hostname:<50}║
# ║  Snapshot    : {snap.timestamp[:19]:<50}║
# ║  Packages    : {len(snap.packages):<4}  Services: {len(snap.services):<4}  Users: {len([u for u in snap.users if u.uid>=1000]):<4}           ║
# ╚══════════════════════════════════════════════════════════════════╝
#
# REVIEW BEFORE RUNNING:
#   1. Verify package names match your target distribution
#   2. Check user UIDs are appropriate for the target system
#   3. Services may need config files deployed before they can start
#   4. Run with --check --diff first
#

"""
    body = yaml.dump(playbook, default_flow_style=False, allow_unicode=True,
                     sort_keys=False, indent=2)
    return header + body


# ══════════════════════════════════════════════════════════════════════════════
# SHELL SCRIPT GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def to_shell(snap: Snapshot) -> str:
    """Generate an idempotent shell script from a snapshot."""
    lines = []
    timestamp = snap.timestamp[:16].replace("T", " ")

    lines += [
        "#!/usr/bin/env bash",
        f"# drift export — {snap.hostname} @ {timestamp}",
        "# Idempotent: safe to run multiple times",
        "set -euo pipefail",
        "DEBIAN_FRONTEND=noninteractive",
        "",
        'log() { echo "[$(date +%H:%M:%S)] $*"; }',
        '[ "$EUID" -eq 0 ] || { echo "Run as root"; exit 1; }',
        "",
    ]

    # Packages by manager
    by_manager: dict[str, list[Package]] = {}
    for pkg in snap.packages:
        by_manager.setdefault(pkg.manager, []).append(pkg)

    if "dpkg" in by_manager or "apt" in by_manager:
        pkgs = sorted(set(
            p.name for p in by_manager.get("dpkg", []) + by_manager.get("apt", [])
        ))
        lines += [
            "# ── Packages ─────────────────────────────────────────────────────",
            "log 'Installing apt packages...'",
            "apt-get update -qq",
        ]
        # Batch into groups of 20
        for i in range(0, len(pkgs), 20):
            batch = pkgs[i:i+20]
            guard = " ".join(f"dpkg -s {p} &>/dev/null" for p in batch[:3])
            lines.append(
                f"apt-get install -y {' '.join(batch)}"
            )
        lines.append("")

    if "rpm" in by_manager or "yum" in by_manager:
        pkgs = sorted(set(
            p.name for p in by_manager.get("rpm", []) + by_manager.get("yum", [])
        ))
        lines += [
            "# ── RPM Packages ─────────────────────────────────────────────────",
            "log 'Installing rpm packages...'",
            f"yum install -y {' '.join(pkgs)}",
            "",
        ]

    if "pip" in by_manager:
        pkgs = sorted(p.name for p in by_manager["pip"])
        lines += [
            "# ── Python Packages ──────────────────────────────────────────────",
            "log 'Installing Python packages...'",
            f"pip3 install {' '.join(pkgs)}",
            "",
        ]

    if "snap" in by_manager:
        lines += ["# ── Snap Packages ────────────────────────────────────────────"]
        for pkg in sorted(by_manager["snap"], key=lambda x: x.name):
            lines.append(
                f'snap list {pkg.name} &>/dev/null || snap install {pkg.name}'
            )
        lines.append("")

    # Users (uid 1000-59999, not nobody/nologin)
    _PSEUDO = {"nobody", "nfsnobody", "nogroup"}
    real_users = [
        u for u in snap.users
        if u.uid >= 1000 and u.uid < 60000
        and u.name not in _PSEUDO
        and u.shell not in ("/bin/false", "/usr/sbin/nologin", "/sbin/nologin")
    ]
    if real_users:
        lines += ["# ── Users ───────────────────────────────────────────────────────"]
        for user in sorted(real_users, key=lambda x: x.uid):
            groups_str = ""
            non_primary = [g for g in user.groups if g != user.name]
            if non_primary:
                groups_str = f" -G {','.join(sorted(non_primary))}"
            lines.append(
                f'id "{user.name}" &>/dev/null || '
                f'useradd -m -u {user.uid} -s {user.shell}{groups_str} "{user.name}"'
            )
        lines.append("")

    # Services
    running_enabled = [
        s for s in snap.services
        if s.state == "active" and s.enabled
        and not any(s.name.startswith(p) for p in ("user@", "session-", "getty@"))
        and not s.name.endswith("@")
    ]
    seen: set[str] = set()
    if running_enabled:
        lines += ["# ── Services ─────────────────────────────────────────────────────"]
        for svc in sorted(running_enabled, key=lambda x: x.name):
            if svc.name in seen:
                continue
            seen.add(svc.name)
            lines.append(f"systemctl enable --now {svc.name} 2>/dev/null || true")
        lines.append("")

    # Sysctl
    notable_sysctl = []
    for param in snap.sysctl:
        defaults = {"net.ipv4.ip_forward": "0", "vm.swappiness": "60"}
        if param.key in defaults and param.value != defaults[param.key]:
            notable_sysctl.append(param)
    if notable_sysctl:
        lines += ["# ── Kernel Parameters ────────────────────────────────────────────"]
        for param in notable_sysctl:
            lines.append(f"sysctl -w {param.key}={param.value}")
        lines.append("sysctl -p")
        lines.append("")

    lines += [
        'log "✅ Done — state reproduced from drift snapshot of ' +
        snap.hostname + ' @ ' + timestamp + '"',
    ]

    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════════
# PACKAGE MANIFEST
# ══════════════════════════════════════════════════════════════════════════════

def to_packages(snap: Snapshot, fmt: str = "text") -> str:
    """
    Export just the package list.
    fmt: "text" (name==version), "json", "requirements" (pip format)
    """
    if fmt == "json":
        return json.dumps(
            [{"name": p.name, "version": p.version, "manager": p.manager}
             for p in sorted(snap.packages, key=lambda x: (x.manager, x.name))],
            indent=2
        )

    if fmt == "requirements":
        pip_pkgs = [p for p in snap.packages if p.manager == "pip"]
        return "\n".join(
            f"{p.name}=={p.version}"
            for p in sorted(pip_pkgs, key=lambda x: x.name)
        )

    # Plain text
    lines = [f"# Packages from {snap.hostname} @ {snap.timestamp[:16]}"]
    by_manager: dict[str, list[Package]] = {}
    for pkg in snap.packages:
        by_manager.setdefault(pkg.manager, []).append(pkg)
    for manager in sorted(by_manager):
        lines.append(f"\n## {manager}")
        for pkg in sorted(by_manager[manager], key=lambda x: x.name):
            lines.append(f"{pkg.name}=={pkg.version}")
    return "\n".join(lines)
