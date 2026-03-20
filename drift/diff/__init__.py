"""
drift/diff/__init__.py

Semantic diff engine. Compares two Snapshots field by field and
produces a list of Change objects describing exactly what changed.

Design principles:
  - Each category has its own comparison logic
  - Changes are human-readable (not raw data blobs)
  - Critical changes (new user, new listening port, removed service) are flagged
"""
from __future__ import annotations
from drift.models import (
    Snapshot, DiffResult, Change,
    Package, Service, Port, User, Group,
    CronJob, SysctlParam, Mount, EnvVar, KernelModule,
)


# ── generic key-based differ ──────────────────────────────────────────────────

def _diff_by_key(
    before: list,
    after:  list,
    key_fn,
    label_fn,
    category:   str,
    to_str,
    critical_added:   bool = False,
    critical_removed: bool = False,
) -> list[Change]:
    """
    Generic list differ: given two lists and a key function,
    find added, removed, and modified items.
    """
    changes = []

    before_map = {key_fn(x): x for x in before}
    after_map  = {key_fn(x): x for x in after}

    all_keys = set(before_map) | set(after_map)

    for key in sorted(all_keys, key=str):
        b = before_map.get(key)
        a = after_map.get(key)

        if b is None and a is not None:
            changes.append(Change(
                category=category, kind="added",
                name=label_fn(a), before=None,
                after=to_str(a), critical=critical_added,
            ))
        elif b is not None and a is None:
            changes.append(Change(
                category=category, kind="removed",
                name=label_fn(b), before=to_str(b),
                after=None, critical=critical_removed,
            ))
        elif b is not None and a is not None:
            b_str = to_str(b)
            a_str = to_str(a)
            if b_str != a_str:
                changes.append(Change(
                    category=category, kind="modified",
                    name=label_fn(a),
                    before=b_str, after=a_str,
                    critical=False,
                ))

    return changes


# ── category-specific differs ─────────────────────────────────────────────────

def _diff_packages(before: list[Package], after: list[Package]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda p: (p.manager, p.name),
        label_fn=lambda p: f"{p.name} ({p.manager})",
        category="package",
        to_str=lambda p: p.version,
        critical_added=False,
        critical_removed=False,
    )


def _diff_services(before: list[Service], after: list[Service]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda s: s.name,
        label_fn=lambda s: s.name,
        category="service",
        to_str=lambda s: f"{s.state} enabled={s.enabled}",
        critical_added=True,   # new service starting is notable
        critical_removed=True, # service disappearing is notable
    )


def _diff_ports(before: list[Port], after: list[Port]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda p: (p.port, p.protocol),
        label_fn=lambda p: f"{p.port}/{p.protocol}",
        category="port",
        to_str=lambda p: f"{p.process} on {p.address}",
        critical_added=True,    # new listening port = security relevant
        critical_removed=False,
    )


def _diff_users(before: list[User], after: list[User]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda u: u.name,
        label_fn=lambda u: u.name,
        category="user",
        to_str=lambda u: f"uid={u.uid} shell={u.shell} groups={','.join(sorted(u.groups))}",
        critical_added=True,    # new user account is critical
        critical_removed=True,  # deleted user account is critical
    )


def _diff_groups(before: list[Group], after: list[Group]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda g: g.name,
        label_fn=lambda g: g.name,
        category="group",
        to_str=lambda g: f"gid={g.gid} members={','.join(sorted(g.members))}",
        critical_added=False,
        critical_removed=False,
    )


def _diff_cron(before: list[CronJob], after: list[CronJob]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda c: (c.owner, c.command[:60]),
        label_fn=lambda c: f"{c.owner}: {c.command[:50]}",
        category="cron",
        to_str=lambda c: f"{c.schedule} [{c.source}]",
        critical_added=True,    # new cron job is notable
        critical_removed=False,
    )


def _diff_sysctl(before: list[SysctlParam], after: list[SysctlParam]) -> list[Change]:
    # Only flag security-relevant sysctl changes as critical
    _CRITICAL_KEYS = {
        "kernel.randomize_va_space",
        "net.ipv4.ip_forward",
        "net.ipv4.conf.all.accept_redirects",
        "net.ipv4.conf.all.send_redirects",
        "net.ipv4.tcp_syncookies",
        "kernel.dmesg_restrict",
        "kernel.kptr_restrict",
        "kernel.perf_event_paranoid",
        "net.ipv6.conf.all.disable_ipv6",
        "kernel.modules_disabled",
    }

    changes = _diff_by_key(
        before, after,
        key_fn=lambda s: s.key,
        label_fn=lambda s: s.key,
        category="sysctl",
        to_str=lambda s: s.value,
        critical_added=False,
        critical_removed=False,
    )

    # Mark security-relevant params as critical
    for c in changes:
        if c.name in _CRITICAL_KEYS:
            c.critical = True

    return changes


def _diff_mounts(before: list[Mount], after: list[Mount]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda m: m.mountpoint,
        label_fn=lambda m: m.mountpoint,
        category="mount",
        to_str=lambda m: f"{m.device} type={m.fstype} opts={m.options}",
        critical_added=False,
        critical_removed=False,
    )


def _diff_env(before: list[EnvVar], after: list[EnvVar]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda e: (e.scope, e.key),
        label_fn=lambda e: f"{e.key} [{e.scope}]",
        category="env_var",
        to_str=lambda e: e.value,
        critical_added=False,
        critical_removed=False,
    )


def _diff_modules(before: list[KernelModule], after: list[KernelModule]) -> list[Change]:
    return _diff_by_key(
        before, after,
        key_fn=lambda m: m.name,
        label_fn=lambda m: m.name,
        category="kernel_module",
        to_str=lambda m: f"size={m.size}",
        critical_added=True,   # new kernel module = security relevant
        critical_removed=False,
    )


# ── main entry ────────────────────────────────────────────────────────────────

def diff_snapshots(before: Snapshot, after: Snapshot) -> DiffResult:
    """
    Compare two snapshots and return a DiffResult with all detected changes.
    """
    changes = []
    changes += _diff_packages(before.packages, after.packages)
    changes += _diff_services(before.services, after.services)
    changes += _diff_ports(before.ports, after.ports)
    changes += _diff_users(before.users, after.users)
    changes += _diff_groups(before.groups, after.groups)
    changes += _diff_cron(before.cron_jobs, after.cron_jobs)
    changes += _diff_sysctl(before.sysctl, after.sysctl)
    changes += _diff_mounts(before.mounts, after.mounts)
    changes += _diff_env(before.env_vars, after.env_vars)
    changes += _diff_modules(before.kernel_modules, after.kernel_modules)

    return DiffResult(
        before_hash=before.digest()[:12],
        after_hash=after.digest()[:12],
        before_time=before.timestamp,
        after_time=after.timestamp,
        hostname=after.hostname,
        changes=changes,
    )


def diff_to_text(result: DiffResult, verbose: bool = False) -> str:
    """Produce a compact human-readable diff string."""
    if not result.changes:
        return "No changes between snapshots."

    lines = [
        f"diff {result.before_hash}..{result.after_hash}",
        f"  {result.before_time[:19]}  →  {result.after_time[:19]}",
        f"  {len(result.changes)} change(s) on {result.hostname}",
        "",
    ]

    for category, changes in sorted(result.by_category.items()):
        lines.append(f"  [{category.upper()}]")
        for c in changes:
            symbol  = {"added": "+", "removed": "-", "modified": "~"}[c.kind]
            flag    = " ⚠" if c.critical else ""
            if c.kind == "added":
                lines.append(f"    {symbol} {c.name}: {c.after}{flag}")
            elif c.kind == "removed":
                lines.append(f"    {symbol} {c.name}: {c.before}{flag}")
            else:  # modified
                if verbose:
                    lines.append(f"    {symbol} {c.name}:")
                    lines.append(f"        before: {c.before}")
                    lines.append(f"        after:  {c.after}{flag}")
                else:
                    lines.append(f"    {symbol} {c.name}: {c.before} → {c.after}{flag}")
        lines.append("")

    return "\n".join(lines)
