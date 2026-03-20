"""
drift/models.py

Core data structures shared across the entire codebase.
Everything that gets snapshotted is represented here.
"""
from __future__ import annotations
import hashlib
import json
import platform
import socket
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# ── Snapshot item types ───────────────────────────────────────────────────────

@dataclass
class Package:
    name:    str
    version: str
    manager: str   # apt, yum, pip, npm, snap, gem …

@dataclass
class Service:
    name:    str
    state:   str   # active, inactive, failed
    enabled: bool
    pid:     Optional[int] = None

@dataclass
class Port:
    port:     int
    protocol: str    # tcp, udp
    process:  str
    pid:      Optional[int] = None
    address:  str = "0.0.0.0"

@dataclass
class User:
    name:    str
    uid:     int
    gid:     int
    shell:   str
    home:    str
    groups:  list[str] = field(default_factory=list)

@dataclass
class Group:
    name:    str
    gid:     int
    members: list[str] = field(default_factory=list)

@dataclass
class CronJob:
    owner:    str
    schedule: str
    command:  str
    source:   str   # "crontab", "/etc/cron.d/name", "/etc/cron.daily/name"

@dataclass
class SysctlParam:
    key:   str
    value: str

@dataclass
class Mount:
    device:    str
    mountpoint: str
    fstype:    str
    options:   str

@dataclass
class EnvVar:
    key:   str
    value: str
    scope: str   # "system" (/etc/environment), "profile" (/etc/profile.d/), "process"

@dataclass
class KernelModule:
    name: str
    size: int
    used_by: list[str] = field(default_factory=list)


# ── Full snapshot ─────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """
    A complete point-in-time capture of server state.
    Stored as a single JSON blob, addressed by its SHA-256 hash.
    """
    timestamp:   str                    # ISO-8601 UTC
    hostname:    str
    os:          str
    kernel:      str
    collector_version: str = "0.1.0"

    packages:    list[Package]          = field(default_factory=list)
    services:    list[Service]          = field(default_factory=list)
    ports:       list[Port]             = field(default_factory=list)
    users:       list[User]             = field(default_factory=list)
    groups:      list[Group]            = field(default_factory=list)
    cron_jobs:   list[CronJob]          = field(default_factory=list)
    sysctl:      list[SysctlParam]      = field(default_factory=list)
    mounts:      list[Mount]            = field(default_factory=list)
    env_vars:    list[EnvVar]           = field(default_factory=list)
    kernel_modules: list[KernelModule]  = field(default_factory=list)

    # Metadata
    errors:      list[str]              = field(default_factory=list)
    duration_ms: int                    = 0

    @classmethod
    def new(cls) -> "Snapshot":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            hostname=socket.gethostname(),
            os=f"{platform.system()} {platform.release()}",
            kernel=platform.version(),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        """Reconstruct a Snapshot from a stored dict."""
        s = cls(
            timestamp=data["timestamp"],
            hostname=data["hostname"],
            os=data.get("os", ""),
            kernel=data.get("kernel", ""),
            collector_version=data.get("collector_version", "0.1.0"),
            duration_ms=data.get("duration_ms", 0),
            errors=data.get("errors", []),
        )
        s.packages   = [Package(**p)    for p in data.get("packages", [])]
        s.services   = [Service(**p)    for p in data.get("services", [])]
        s.ports      = [Port(**p)       for p in data.get("ports", [])]
        s.users      = [User(**p)       for p in data.get("users", [])]
        s.groups     = [Group(**p)      for p in data.get("groups", [])]
        s.cron_jobs  = [CronJob(**p)    for p in data.get("cron_jobs", [])]
        s.sysctl     = [SysctlParam(**p) for p in data.get("sysctl", [])]
        s.mounts     = [Mount(**p)      for p in data.get("mounts", [])]
        s.env_vars   = [EnvVar(**p)     for p in data.get("env_vars", [])]
        s.kernel_modules = [KernelModule(**p) for p in data.get("kernel_modules", [])]
        return s

    def digest(self) -> str:
        """SHA-256 of the sorted JSON — content-addressable like git."""
        raw = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


# ── Commit (log entry) ────────────────────────────────────────────────────────

@dataclass
class Commit:
    """
    One entry in the drift log — analogous to a git commit.
    """
    hash:         str      # first 12 chars of snapshot digest
    full_hash:    str      # full SHA-256
    timestamp:    str      # ISO-8601 UTC
    hostname:     str
    message:      str      # auto-generated summary, e.g. "+3 packages, -1 service"
    change_count: int      # total number of individual changes
    author:       str      # SSH user who triggered the snapshot, or "scheduled"
    trigger:      str      # "scheduled" | "manual" | "ssh_login" | "ssh_logout"
    parent:       Optional[str] = None   # hash of previous commit

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Commit":
        return cls(**data)


# ── Diff types ────────────────────────────────────────────────────────────────

@dataclass
class Change:
    """One individual change detected between two snapshots."""
    category:  str     # "package", "service", "port", "user", etc.
    kind:      str     # "added", "removed", "modified"
    name:      str     # human-readable identifier
    before:    Any     # value before (None if added)
    after:     Any     # value after (None if removed)
    critical:  bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiffResult:
    """Complete diff between two snapshots."""
    before_hash: str
    after_hash:  str
    before_time: str
    after_time:  str
    hostname:    str
    changes:     list[Change] = field(default_factory=list)

    @property
    def by_category(self) -> dict[str, list[Change]]:
        result: dict[str, list[Change]] = {}
        for c in self.changes:
            result.setdefault(c.category, []).append(c)
        return result

    @property
    def summary(self) -> str:
        if not self.changes:
            return "no changes"
        counts: dict[str, int] = {}
        for c in self.changes:
            key = f"{'+' if c.kind == 'added' else ('-' if c.kind == 'removed' else '~')}{c.category}"
            counts[key] = counts.get(key, 0) + 1
        return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
