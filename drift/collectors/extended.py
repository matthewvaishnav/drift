"""
drift/collectors/extended.py

Additional collectors for non-Debian/RHEL systems:
  - Alpine Linux (apk)
  - Arch Linux (pacman)
  - macOS (brew)
  - Additional package sources

Also contains the inotify-based filesystem watcher for drift watch.
"""
from __future__ import annotations
import os
import re
import subprocess
import time
from typing import Optional
from drift.models import Package


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 10) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _available(binary: str) -> bool:
    import shutil
    return shutil.which(binary) is not None


# ══════════════════════════════════════════════════════════════════════════════
# ALPINE LINUX — apk
# ══════════════════════════════════════════════════════════════════════════════

def collect_apk() -> tuple[list[Package], list[str]]:
    """Alpine Linux apk package list."""
    packages: list[Package] = []
    errors:   list[str]     = []

    if not _available("apk"):
        return packages, errors

    out = _run(["apk", "list", "--installed"], timeout=15)
    if not out:
        errors.append("apk list returned no output")
        return packages, errors

    # Format: "nginx-1.24.0-r6 x86_64 {nginx} (BSD-2-Clause) [installed]"
    for line in out.strip().splitlines():
        m = re.match(r"^(\S+)-(\d[\w.\-]+)\s", line)
        if m:
            packages.append(Package(
                name=m.group(1),
                version=m.group(2).rstrip("-"),
                manager="apk",
            ))
        else:
            # Alternative format: "nginx-1.24.0-r6"
            parts = line.split()
            if parts:
                token = parts[0]
                # Find the last occurrence of -<version>
                vm = re.search(r"-(\d[\w.\-]*)$", token)
                if vm:
                    name    = token[:-(len(vm.group(0)))]
                    version = vm.group(1)
                    packages.append(Package(name=name, version=version, manager="apk"))

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# ARCH LINUX — pacman
# ══════════════════════════════════════════════════════════════════════════════

def collect_pacman() -> tuple[list[Package], list[str]]:
    """Arch Linux pacman package list."""
    packages: list[Package] = []
    errors:   list[str]     = []

    if not _available("pacman"):
        return packages, errors

    out = _run(["pacman", "-Q"], timeout=15)
    if not out:
        errors.append("pacman -Q returned no output")
        return packages, errors

    # Format: "nginx 1.24.0-1"
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            packages.append(Package(
                name=parts[0],
                version=parts[1],
                manager="pacman",
            ))

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# macOS — Homebrew
# ══════════════════════════════════════════════════════════════════════════════

def collect_brew() -> tuple[list[Package], list[str]]:
    """macOS Homebrew package list."""
    packages: list[Package] = []
    errors:   list[str]     = []

    if not _available("brew"):
        return packages, errors

    # Formulae
    out = _run(["brew", "list", "--versions", "--formula"], timeout=30)
    if out:
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                packages.append(Package(
                    name=parts[0],
                    version=parts[1],
                    manager="brew",
                ))

    # Casks
    out_cask = _run(["brew", "list", "--versions", "--cask"], timeout=30)
    if out_cask:
        for line in out_cask.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                packages.append(Package(
                    name=parts[0],
                    version=parts[1],
                    manager="brew-cask",
                ))

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# CARGO (Rust) — global installs
# ══════════════════════════════════════════════════════════════════════════════

def collect_cargo() -> tuple[list[Package], list[str]]:
    packages: list[Package] = []
    errors:   list[str]     = []

    if not _available("cargo"):
        return packages, errors

    out = _run(["cargo", "install", "--list"], timeout=20)
    if not out:
        return packages, errors

    current_name = None
    for line in out.strip().splitlines():
        # "ripgrep v13.0.0:"
        m = re.match(r"^(\S+)\s+v([\d.]+):", line)
        if m:
            packages.append(Package(
                name=m.group(1),
                version=m.group(2),
                manager="cargo",
            ))

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-DETECT DISTRO AND PICK THE RIGHT PACKAGE COLLECTOR
# ══════════════════════════════════════════════════════════════════════════════

def detect_distro() -> str:
    """Detect Linux distribution family."""
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
        if "alpine" in content:   return "alpine"
        if "arch"   in content:   return "arch"
        if "fedora" in content or "rhel" in content or "centos" in content:
            return "rhel"
        if "debian" in content or "ubuntu" in content:
            return "debian"
    except IOError:
        pass

    import platform
    if platform.system() == "Darwin":
        return "macos"
    return "unknown"


def collect_packages_extended() -> tuple[list[Package], list[str]]:
    """
    Auto-detect distro and collect packages from all available managers.
    Called by run_all() as a supplement to the primary package collector.
    """
    packages: list[Package] = []
    errors:   list[str]     = []

    distro = detect_distro()

    if distro == "alpine":
        p, e = collect_apk()
        packages.extend(p)
        errors.extend(e)
    elif distro == "arch":
        p, e = collect_pacman()
        packages.extend(p)
        errors.extend(e)
    elif distro == "macos":
        p, e = collect_brew()
        packages.extend(p)
        errors.extend(e)

    # Always try cargo regardless of distro
    if _available("cargo"):
        p, e = collect_cargo()
        packages.extend(p)
        errors.extend(e)

    return packages, errors


# ══════════════════════════════════════════════════════════════════════════════
# INOTIFY WATCHER — drift watch
# ══════════════════════════════════════════════════════════════════════════════

# Paths that trigger an immediate snapshot when modified
_WATCH_PATHS = [
    "/etc/passwd",
    "/etc/group",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/crontab",
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/cron.hourly",
    "/etc/cron.weekly",
    "/etc/cron.monthly",
    "/etc/profile.d",
    "/etc/environment",
    "/etc/apt/sources.list",
    "/etc/yum.repos.d",
    "/etc/systemd/system",
    "/usr/lib/systemd/system",
    "/etc/ssh/sshd_config",
    "/etc/pam.d",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/fstab",
    "/etc/sysctl.conf",
    "/etc/sysctl.d",
]

# Minimum seconds between watch-triggered snapshots (debounce)
_DEBOUNCE_SECONDS = 5


def watch(interval: int = 3600, on_change=None) -> None:
    """
    Watch critical filesystem paths using inotify (Linux) or polling.
    Triggers a snapshot immediately when a watched path is modified.
    Also takes periodic snapshots at `interval` seconds.

    on_change: callback(trigger, author) — defaults to take_snapshot
    """
    import logging
    logger = logging.getLogger("drift.watch")

    if on_change is None:
        from drift.daemon import take_snapshot
        on_change = take_snapshot

    # Try inotify first (Linux)
    try:
        _watch_inotify(interval, on_change, logger)
    except (ImportError, OSError, AttributeError):
        # Fall back to polling
        logger.info("inotify not available, falling back to polling")
        _watch_polling(interval, on_change, logger)


def _watch_inotify(interval: int, on_change, logger) -> None:
    """
    Use inotifywait (external tool) or /proc/sys/fs/inotify via ctypes.
    This is the preferred approach on Linux.
    """
    import shutil
    if not shutil.which("inotifywait"):
        raise ImportError("inotifywait not found")

    import signal
    import threading

    last_snap    = time.monotonic()
    last_trigger = 0.0
    stop         = threading.Event()

    def _handle_signal(sig, frame):
        stop.set()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # Filter to existing paths
    watch_paths = [p for p in _WATCH_PATHS if os.path.exists(p)]

    logger.info(f"Watching {len(watch_paths)} paths with inotify")

    # Run inotifywait in monitor mode
    cmd = [
        "inotifywait", "--monitor", "--recursive",
        "--format", "%w%f",
        "--event", "modify,create,delete,move",
    ] + watch_paths

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)

    def _read_events():
        nonlocal last_trigger
        for line in proc.stdout:
            if stop.is_set():
                break
            path = line.strip()
            now  = time.monotonic()
            if now - last_trigger < _DEBOUNCE_SECONDS:
                continue
            last_trigger = now
            logger.info(f"File change detected: {path} — triggering snapshot")
            try:
                on_change(trigger="file_change", author=f"inotify:{path[:60]}")
            except Exception as e:
                logger.error(f"Snapshot failed after file change: {e}")

    t = threading.Thread(target=_read_events, daemon=True)
    t.start()

    # Periodic snapshots
    while not stop.is_set():
        now = time.monotonic()
        if now - last_snap >= interval:
            try:
                on_change(trigger="scheduled")
                last_snap = time.monotonic()
            except Exception as e:
                logger.error(f"Periodic snapshot failed: {e}")
        time.sleep(10)

    proc.terminate()


def _watch_polling(interval: int, on_change, logger) -> None:
    """
    Fallback watcher: poll file mtimes every 30 seconds.
    Less responsive than inotify but works everywhere.
    """
    import signal

    stop     = False
    last_snap = time.monotonic()
    last_trigger = 0.0

    def _handle_signal(sig, frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # Build initial mtime map
    mtimes: dict[str, float] = {}
    for path in _WATCH_PATHS:
        try:
            mtimes[path] = os.stat(path).st_mtime
        except OSError:
            pass

    logger.info(f"Polling {len(mtimes)} paths every 30s")

    while not stop:
        time.sleep(30)
        now = time.monotonic()

        # Check mtimes
        changed_paths = []
        for path in _WATCH_PATHS:
            try:
                mtime = os.stat(path).st_mtime
                if path in mtimes and mtime != mtimes[path]:
                    changed_paths.append(path)
                mtimes[path] = mtime
            except OSError:
                pass

        if changed_paths and (now - last_trigger) >= _DEBOUNCE_SECONDS:
            last_trigger = now
            logger.info(f"Detected changes in: {changed_paths[:3]}")
            try:
                on_change(trigger="file_change",
                         author=f"poll:{','.join(changed_paths[:2])}")
            except Exception as e:
                logger.error(f"Snapshot failed: {e}")

        # Periodic snapshot
        if now - last_snap >= interval:
            try:
                on_change(trigger="scheduled")
                last_snap = time.monotonic()
            except Exception as e:
                logger.error(f"Periodic snapshot failed: {e}")
