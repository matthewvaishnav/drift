"""
drift/collectors/__init__.py

One collector per state category. Each collector:
  - has a collect() function that returns a list of model objects
  - catches its own exceptions and appends to errors[]
  - runs in under 5 seconds on a typical server
  - requires NO external dependencies — only stdlib + standard system tools

All collectors are called by run_all() which returns a complete Snapshot.
"""
from __future__ import annotations
import os
import re
import subprocess
import time
from typing import Optional

from drift.models import (
    Snapshot, Package, Service, Port, User, Group,
    CronJob, SysctlParam, Mount, EnvVar, KernelModule,
)


# ── helper ────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 10) -> Optional[str]:
    """Run a command, return stdout, or None on error."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, errors="replace"
        )
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _available(binary: str) -> bool:
    import shutil
    return shutil.which(binary) is not None


# ══════════════════════════════════════════════════════════════════════════════
# PACKAGES
# ══════════════════════════════════════════════════════════════════════════════

def collect_packages() -> tuple[list[Package], list[str]]:
    packages: list[Package] = []
    errors:   list[str]     = []

    # ── dpkg (Debian/Ubuntu) ──────────────────────────────────────────────────
    if _available("dpkg-query"):
        out = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n"])
        if out:
            for line in out.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and "install ok installed" in parts[2]:
                    packages.append(Package(
                        name=parts[0].strip(),
                        version=parts[1].strip(),
                        manager="dpkg",
                    ))

    # ── rpm (RHEL/CentOS/Fedora) ──────────────────────────────────────────────
    elif _available("rpm"):
        out = _run(["rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n"])
        if out:
            for line in out.strip().splitlines():
                parts = line.split("\t")
                if len(parts) == 2:
                    packages.append(Package(
                        name=parts[0].strip(),
                        version=parts[1].strip(),
                        manager="rpm",
                    ))

    # ── pip (system Python packages) ─────────────────────────────────────────
    for pip_bin in ("pip3", "pip"):
        if _available(pip_bin):
            out = _run([pip_bin, "list", "--format=freeze"], timeout=15)
            if out:
                for line in out.strip().splitlines():
                    if "==" in line:
                        name, version = line.split("==", 1)
                        packages.append(Package(
                            name=name.strip().lower(),
                            version=version.strip(),
                            manager="pip",
                        ))
            break

    # ── snap ─────────────────────────────────────────────────────────────────
    if _available("snap"):
        out = _run(["snap", "list"])
        if out:
            for line in out.strip().splitlines()[1:]:  # skip header
                parts = line.split()
                if len(parts) >= 2:
                    packages.append(Package(
                        name=parts[0],
                        version=parts[1],
                        manager="snap",
                    ))

    # ── npm global ────────────────────────────────────────────────────────────
    if _available("npm"):
        out = _run(["npm", "list", "-g", "--depth=0", "--parseable"], timeout=15)
        if out:
            for line in out.strip().splitlines()[1:]:
                # /usr/lib/node_modules/npm  →  npm
                m = re.search(r"node_modules/(.+)$", line)
                if m:
                    pkg_ver = m.group(1)
                    if "@" in pkg_ver and not pkg_ver.startswith("@"):
                        name, version = pkg_ver.rsplit("@", 1)
                    else:
                        name, version = pkg_ver, "unknown"
                    packages.append(Package(name=name, version=version, manager="npm"))

    # ── gem ───────────────────────────────────────────────────────────────────
    if _available("gem"):
        out = _run(["gem", "list", "--local", "--no-details"])
        if out:
            for line in out.strip().splitlines():
                m = re.match(r"^(\S+)\s+\((.+)\)$", line)
                if m:
                    packages.append(Package(
                        name=m.group(1), version=m.group(2).split(",")[0].strip(),
                        manager="gem",
                    ))

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# SERVICES
# ══════════════════════════════════════════════════════════════════════════════

def collect_services() -> tuple[list[Service], list[str]]:
    services: list[Service] = []
    errors:   list[str]     = []

    if not _available("systemctl"):
        errors.append("systemctl not available — skipping service collection")
        return services, errors

    # List all service units (both active and inactive)
    out = _run(["systemctl", "list-units", "--type=service",
                "--all", "--no-pager", "--no-legend",
                "--output=json"], timeout=15)

    if not out:
        # Fallback to plain text parsing
        out_plain = _run(["systemctl", "list-units", "--type=service",
                          "--all", "--no-pager", "--no-legend"])
        if out_plain:
            for line in out_plain.strip().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    name    = parts[0].replace(".service", "")
                    state   = parts[2]   # active/inactive/failed
                    enabled_out = _run(["systemctl", "is-enabled", parts[0]], timeout=3)
                    enabled = enabled_out.strip() == "enabled" if enabled_out else False

                    # Get PID for active services
                    pid = None
                    if state == "active":
                        show = _run(["systemctl", "show", parts[0],
                                     "--property=MainPID"], timeout=3)
                        if show and "=" in show:
                            try:
                                pid = int(show.split("=")[1].strip())
                                if pid == 0:
                                    pid = None
                            except ValueError:
                                pass

                    services.append(Service(
                        name=name, state=state, enabled=enabled, pid=pid
                    ))
        return services, errors

    # Parse JSON output
    try:
        import json
        units = json.loads(out)
        for u in units:
            name    = u.get("unit", "").replace(".service", "")
            state   = u.get("active", "unknown")
            sub     = u.get("sub", "")
            enabled_out = _run(["systemctl", "is-enabled",
                                 u.get("unit", "")], timeout=3)
            enabled = (enabled_out or "").strip() in ("enabled", "static")
            services.append(Service(name=name, state=state, enabled=enabled))
    except Exception as e:
        errors.append(f"service JSON parse error: {e}")

    return services, errors


# ══════════════════════════════════════════════════════════════════════════════
# OPEN PORTS
# ══════════════════════════════════════════════════════════════════════════════

def collect_ports() -> tuple[list[Port], list[str]]:
    ports:  list[Port] = []
    errors: list[str]  = []

    # Try ss first (modern), fall back to netstat
    if _available("ss"):
        # -t TCP, -u UDP, -l listening, -n numeric, -p process, -H no header
        out = _run(["ss", "-tlunpH"], timeout=10)
        if out:
            for line in out.strip().splitlines():
                ports.extend(_parse_ss_line(line))
            return ports, errors

    if _available("netstat"):
        out = _run(["netstat", "-tlunp"], timeout=10)
        if out:
            for line in out.strip().splitlines()[2:]:
                p = _parse_netstat_line(line)
                if p:
                    ports.append(p)
            return ports, errors

    # Last resort: read /proc/net/tcp and /proc/net/udp directly
    for proto, path in [("tcp", "/proc/net/tcp"), ("udp", "/proc/net/udp")]:
        try:
            with open(path) as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    state = parts[3]
                    if state != "0A":   # 0A = TCP_LISTEN
                        continue
                    local = parts[1]
                    addr_hex, port_hex = local.split(":")
                    port_num = int(port_hex, 16)
                    addr = _hex_to_ip(addr_hex)
                    ports.append(Port(
                        port=port_num, protocol=proto,
                        process="(unknown)", address=addr,
                    ))
        except (IOError, ValueError):
            pass

    return ports, errors


def _parse_ss_line(line: str) -> list[Port]:
    """Parse a line from `ss -tlunpH`."""
    results = []
    line = line.strip()
    if not line:
        return results
    parts = line.split()
    if len(parts) < 5:
        return results

    proto = parts[0].lower().rstrip(",")   # tcp, udp, tcp6, udp6
    local_addr = parts[4]

    # Extract port from address like "0.0.0.0:22" or "*:80" or "[::]:443"
    m = re.search(r":(\d+)$", local_addr)
    if not m:
        return results
    port_num = int(m.group(1))
    addr     = local_addr[:-(len(m.group(0)))] or "0.0.0.0"

    # Extract process name from users:(("sshd",pid=1234,fd=3))
    process = "(unknown)"
    pid     = None
    if len(parts) >= 6:
        proc_m = re.search(r'"([^"]+)",pid=(\d+)', parts[-1])
        if proc_m:
            process = proc_m.group(1)
            pid     = int(proc_m.group(2))

    results.append(Port(
        port=port_num,
        protocol="tcp" if "tcp" in proto else "udp",
        process=process, pid=pid, address=addr,
    ))
    return results


def _parse_netstat_line(line: str) -> Optional[Port]:
    parts = line.split()
    if len(parts) < 4:
        return None
    proto     = parts[0].lower()
    local     = parts[3]
    state     = parts[5] if len(parts) > 5 else ""
    if "tcp" in proto and state != "LISTEN":
        return None
    m = re.search(r":(\d+)$", local)
    if not m:
        return None
    port_num = int(m.group(1))
    process  = parts[-1].split("/")[-1] if "/" in (parts[-1] if parts else "") else "(unknown)"
    return Port(port=port_num, protocol="tcp" if "tcp" in proto else "udp",
                process=process)


def _hex_to_ip(hex_addr: str) -> str:
    try:
        n = int(hex_addr, 16)
        return f"{n & 0xFF}.{(n >> 8) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 24) & 0xFF}"
    except ValueError:
        return "0.0.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════

def collect_users() -> tuple[list[User], list[str]]:
    users:  list[User] = []
    errors: list[str]  = []

    # Read /etc/passwd
    try:
        with open("/etc/passwd") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) < 7:
                    continue
                name, _, uid, gid, _, home, shell = parts[:7]
                try:
                    uid_int = int(uid)
                    gid_int = int(gid)
                except ValueError:
                    continue

                # Get supplementary groups
                groups_out = _run(["id", "-Gn", name], timeout=3)
                groups = groups_out.strip().split() if groups_out else []

                users.append(User(
                    name=name, uid=uid_int, gid=gid_int,
                    shell=shell, home=home, groups=groups,
                ))
    except IOError as e:
        errors.append(f"Could not read /etc/passwd: {e}")

    return users, errors


# ══════════════════════════════════════════════════════════════════════════════
# GROUPS
# ══════════════════════════════════════════════════════════════════════════════

def collect_groups() -> tuple[list[Group], list[str]]:
    groups: list[Group] = []
    errors: list[str]   = []

    try:
        with open("/etc/group") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) < 4:
                    continue
                name, _, gid, members_str = parts[:4]
                try:
                    gid_int = int(gid)
                except ValueError:
                    continue
                members = [m for m in members_str.split(",") if m]
                groups.append(Group(name=name, gid=gid_int, members=members))
    except IOError as e:
        errors.append(f"Could not read /etc/group: {e}")

    return groups, errors


# ══════════════════════════════════════════════════════════════════════════════
# CRON JOBS
# ══════════════════════════════════════════════════════════════════════════════

def collect_cron() -> tuple[list[CronJob], list[str]]:
    cron_jobs: list[CronJob] = []
    errors:    list[str]     = []

    # System crontab
    try:
        with open("/etc/crontab") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 7:
                    schedule = " ".join(parts[:5])
                    owner    = parts[5]
                    command  = " ".join(parts[6:])
                    cron_jobs.append(CronJob(
                        owner=owner, schedule=schedule,
                        command=command, source="/etc/crontab",
                    ))
    except IOError:
        pass

    # /etc/cron.d/
    cron_d = "/etc/cron.d"
    if os.path.isdir(cron_d):
        for filename in os.listdir(cron_d):
            filepath = os.path.join(cron_d, filename)
            try:
                with open(filepath) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split()
                        if len(parts) >= 7:
                            schedule = " ".join(parts[:5])
                            owner    = parts[5]
                            command  = " ".join(parts[6:])
                            cron_jobs.append(CronJob(
                                owner=owner, schedule=schedule,
                                command=command, source=filepath,
                            ))
            except IOError:
                pass

    # User crontabs via crontab -l for each user
    try:
        with open("/etc/passwd") as f:
            users = [l.split(":")[0] for l in f if l.strip() and not l.startswith("#")]
    except IOError:
        users = []

    for user in users:
        out = _run(["crontab", "-l", "-u", user], timeout=5)
        if out:
            for line in out.strip().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    schedule = " ".join(parts[:5])
                    command  = " ".join(parts[5:])
                    cron_jobs.append(CronJob(
                        owner=user, schedule=schedule,
                        command=command, source="crontab",
                    ))

    # cron.daily / cron.hourly / cron.weekly / cron.monthly script names
    for period in ("hourly", "daily", "weekly", "monthly"):
        d = f"/etc/cron.{period}"
        if os.path.isdir(d):
            for script in os.listdir(d):
                if not script.startswith("."):
                    cron_jobs.append(CronJob(
                        owner="root", schedule=f"@{period}",
                        command=script, source=d,
                    ))

    return cron_jobs, errors


# ══════════════════════════════════════════════════════════════════════════════
# SYSCTL
# ══════════════════════════════════════════════════════════════════════════════

def collect_sysctl() -> tuple[list[SysctlParam], list[str]]:
    params: list[SysctlParam] = []
    errors: list[str]         = []

    if not _available("sysctl"):
        errors.append("sysctl not available")
        return params, errors

    out = _run(["sysctl", "-a"], timeout=15)
    if not out:
        errors.append("sysctl -a returned no output")
        return params, errors

    for line in out.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip()
            # Skip extremely verbose or binary params
            if not key or len(value) > 500:
                continue
            params.append(SysctlParam(key=key, value=value))

    return params, errors


# ══════════════════════════════════════════════════════════════════════════════
# MOUNTS
# ══════════════════════════════════════════════════════════════════════════════

def collect_mounts() -> tuple[list[Mount], list[str]]:
    mounts: list[Mount] = []
    errors: list[str]   = []

    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    device, mountpoint, fstype, options = parts[:4]
                    # Skip virtual/kernel filesystems
                    if fstype in ("proc", "sysfs", "devtmpfs", "devpts", "tmpfs",
                                  "cgroup", "cgroup2", "pstore", "securityfs",
                                  "debugfs", "hugetlbfs", "mqueue", "fusectl",
                                  "bpf", "tracefs", "configfs", "ramfs",
                                  "efivarfs", "autofs", "binfmt_misc"):
                        continue
                    mounts.append(Mount(
                        device=device, mountpoint=mountpoint,
                        fstype=fstype, options=options,
                    ))
    except IOError as e:
        errors.append(f"Could not read /proc/mounts: {e}")

    return mounts, errors


# ══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES (system-wide only)
# ══════════════════════════════════════════════════════════════════════════════

def collect_env() -> tuple[list[EnvVar], list[str]]:
    env_vars: list[EnvVar] = []
    errors:   list[str]    = []

    # /etc/environment
    try:
        with open("/etc/environment") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env_vars.append(EnvVar(
                        key=key.strip(),
                        value=value.strip().strip('"\''),
                        scope="system",
                    ))
    except IOError:
        pass

    # /etc/profile.d/*.sh (extract exported variable assignments)
    profile_d = "/etc/profile.d"
    if os.path.isdir(profile_d):
        for fname in os.listdir(profile_d):
            if fname.endswith(".sh"):
                try:
                    with open(os.path.join(profile_d, fname)) as f:
                        for line in f:
                            line = line.strip()
                            # Match: export VAR=value or VAR=value
                            m = re.match(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$", line)
                            if m:
                                env_vars.append(EnvVar(
                                    key=m.group(1),
                                    value=m.group(2).strip('"\''),
                                    scope=f"profile:{fname}",
                                ))
                except IOError:
                    pass

    return env_vars, errors


# ══════════════════════════════════════════════════════════════════════════════
# KERNEL MODULES
# ══════════════════════════════════════════════════════════════════════════════

def collect_kernel_modules() -> tuple[list[KernelModule], list[str]]:
    modules: list[KernelModule] = []
    errors:  list[str]          = []

    try:
        with open("/proc/modules") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                name = parts[0]
                try:
                    size = int(parts[1])
                except ValueError:
                    size = 0
                used_by = [u for u in parts[3].split(",") if u and u != "-"]
                modules.append(KernelModule(name=name, size=size, used_by=used_by))
    except IOError as e:
        errors.append(f"Could not read /proc/modules: {e}")

    return modules, errors


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY — run all collectors
# ══════════════════════════════════════════════════════════════════════════════

def run_all(
    include: Optional[set[str]] = None,
    exclude: Optional[set[str]] = None,
) -> Snapshot:
    """
    Run all collectors and return a complete Snapshot.

    include: if set, only run these collectors
    exclude: skip these collectors
    """
    snapshot  = Snapshot.new()
    t_start   = time.monotonic()

    ALL_COLLECTORS = {
        "packages":  collect_packages,
        "services":  collect_services,
        "ports":     collect_ports,
        "users":     collect_users,
        "groups":    collect_groups,
        "cron":      collect_cron,
        "sysctl":    collect_sysctl,
        "mounts":    collect_mounts,
        "env":       collect_env,
        "modules":   collect_kernel_modules,
    }

    for name, fn in ALL_COLLECTORS.items():
        if include and name not in include:
            continue
        if exclude and name in exclude:
            continue
        try:
            items, errs = fn()
            snapshot.errors.extend(errs)
            # Map collector name to snapshot field
            field_map = {
                "packages": "packages", "services": "services",
                "ports":    "ports",    "users":    "users",
                "groups":   "groups",   "cron":     "cron_jobs",
                "sysctl":   "sysctl",   "mounts":   "mounts",
                "env":      "env_vars", "modules":  "kernel_modules",
            }
            setattr(snapshot, field_map[name], items)
        except Exception as e:
            snapshot.errors.append(f"Collector '{name}' crashed: {e}")

    snapshot.duration_ms = int((time.monotonic() - t_start) * 1000)
    return snapshot
