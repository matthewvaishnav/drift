"""
drift/daemon.py

Background daemon that takes periodic snapshots.
Can run as:
  - A systemd service (see systemd/drift.service)
  - A simple background process: drift daemon start
  - Triggered by PAM/SSH hooks for instant capture on login/logout

The daemon watches for:
  1. Scheduled intervals (default: every hour)
  2. SSH login events (PAM_TYPE=open_session)
  3. SSH logout events (PAM_TYPE=close_session)
  4. Manual triggers via `drift snapshot`

On change detection it:
  1. Saves the snapshot
  2. Writes a commit to the log
  3. Optionally sends a Slack/webhook alert for critical changes
"""
from __future__ import annotations
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("drift.daemon")


# ── configuration ─────────────────────────────────────────────────────────────

class DaemonConfig:
    def __init__(self):
        self.interval_seconds:   int  = int(os.environ.get("DRIFT_INTERVAL",  "3600"))  # 1h default
        self.alert_on_critical:  bool = os.environ.get("DRIFT_ALERT_CRITICAL", "0") == "1"
        self.slack_webhook:      Optional[str] = os.environ.get("DRIFT_SLACK_WEBHOOK")
        self.pid_file:           Path = Path(os.environ.get("DRIFT_PID_FILE", "/tmp/drift.pid"))
        self.log_file:           Optional[str] = os.environ.get("DRIFT_LOG_FILE")
        self.exclude_collectors: set  = set(
            os.environ.get("DRIFT_EXCLUDE_COLLECTORS", "").split(",")
        ) - {""}


# ── snapshot trigger ──────────────────────────────────────────────────────────

def take_snapshot(
    trigger: str = "scheduled",
    author:  str = "system",
    exclude: Optional[set] = None,
) -> dict:
    """
    Run collectors, store snapshot, create commit.
    Returns summary dict.
    """
    from drift.collectors import run_all
    from drift.storage    import commit_snapshot

    logger.info(f"Taking snapshot: trigger={trigger} author={author}")
    t_start  = time.monotonic()
    snapshot = run_all(exclude=exclude)
    commit   = commit_snapshot(snapshot, trigger=trigger, author=author)
    elapsed  = int((time.monotonic() - t_start) * 1000)

    logger.info(
        f"Snapshot complete: hash={commit.hash} "
        f"changes={commit.change_count} "
        f"duration={elapsed}ms"
    )

    return {
        "hash":         commit.hash,
        "changes":      commit.change_count,
        "message":      commit.message,
        "duration_ms":  elapsed,
        "errors":       snapshot.errors,
    }


def _send_alert(commit, changes: list) -> None:
    """Send Slack alert for critical changes."""
    cfg = DaemonConfig()
    if not cfg.slack_webhook:
        return

    critical = [c for c in changes if c.critical]
    if not critical:
        return

    import urllib.request
    lines = [f"• `{c.category}` **{c.name}** ({c.kind})" for c in critical[:10]]
    if len(critical) > 10:
        lines.append(f"• ...and {len(critical)-10} more")

    payload = {
        "text": (
            f"*⚠️ drift: critical changes on `{commit.hostname}`*\n"
            f"Commit `{commit.hash}` by `{commit.author}` ({commit.trigger})\n"
            + "\n".join(lines)
        )
    }
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            cfg.slack_webhook,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"Slack alert failed: {e}")


# ── daemon main loop ──────────────────────────────────────────────────────────

def _write_pid() -> None:
    cfg = DaemonConfig()
    cfg.pid_file.write_text(str(os.getpid()))


def _remove_pid() -> None:
    cfg = DaemonConfig()
    try:
        cfg.pid_file.unlink()
    except FileNotFoundError:
        pass


def run_daemon() -> None:
    """
    Main daemon loop. Runs forever, taking snapshots at configured interval.
    Handles SIGTERM and SIGINT gracefully.
    """
    cfg = DaemonConfig()
    _write_pid()

    # Set up logging
    log_level = logging.INFO
    if cfg.log_file:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=[
                logging.FileHandler(cfg.log_file),
                logging.StreamHandler(sys.stderr),
            ],
        )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    logger.info(f"drift daemon started (pid={os.getpid()}, interval={cfg.interval_seconds}s)")

    stop_event = False

    def _handle_signal(sig, frame):
        nonlocal stop_event
        logger.info(f"Received signal {sig}, stopping")
        stop_event = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # Take an immediate snapshot on startup
    try:
        result = take_snapshot(trigger="daemon_start", exclude=cfg.exclude_collectors)
        logger.info(f"Startup snapshot: {result['hash']} ({result['changes']} changes)")
    except Exception as e:
        logger.error(f"Startup snapshot failed: {e}")

    last_snapshot = time.monotonic()

    while not stop_event:
        now     = time.monotonic()
        elapsed = now - last_snapshot

        if elapsed >= cfg.interval_seconds:
            try:
                result = take_snapshot(trigger="scheduled", exclude=cfg.exclude_collectors)
                logger.info(f"Scheduled snapshot: {result['hash']} ({result['changes']} changes)")
                if result.get("errors"):
                    logger.warning(f"Collector errors: {result['errors']}")
            except Exception as e:
                logger.error(f"Scheduled snapshot failed: {e}")
            last_snapshot = time.monotonic()

        # Sleep in small increments so we catch stop_event promptly
        time.sleep(min(30, cfg.interval_seconds))

    logger.info("drift daemon stopped")
    _remove_pid()


def start_daemon() -> None:
    """Fork to background and run the daemon."""
    cfg = DaemonConfig()

    # Check if already running
    if cfg.pid_file.exists():
        try:
            pid = int(cfg.pid_file.read_text().strip())
            os.kill(pid, 0)   # signal 0 = just check if process exists
            print(f"drift daemon already running (pid={pid})", file=sys.stderr)
            sys.exit(1)
        except (ValueError, ProcessLookupError):
            pass   # stale pid file

    try:
        pid = os.fork()
        if pid > 0:
            print(f"drift daemon started (pid={pid})")
            return
    except OSError as e:
        print(f"fork failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Child: detach from terminal
    os.setsid()
    os.umask(0)

    # Redirect standard file descriptors
    with open(os.devnull, "r") as f:
        os.dup2(f.fileno(), sys.stdin.fileno())

    run_daemon()


def stop_daemon() -> None:
    """Stop a running daemon by sending SIGTERM."""
    cfg = DaemonConfig()
    if not cfg.pid_file.exists():
        print("drift daemon is not running", file=sys.stderr)
        return

    try:
        pid = int(cfg.pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to drift daemon (pid={pid})")
    except (ValueError, ProcessLookupError):
        print("drift daemon is not running (stale pid file)", file=sys.stderr)
        _remove_pid()


def daemon_status() -> dict:
    """Return daemon status."""
    cfg = DaemonConfig()
    if not cfg.pid_file.exists():
        return {"running": False}

    try:
        pid = int(cfg.pid_file.read_text().strip())
        os.kill(pid, 0)
        return {"running": True, "pid": pid}
    except (ValueError, ProcessLookupError):
        return {"running": False, "stale_pid": True}


# ══════════════════════════════════════════════════════════════════════════════
# PAM / SSH HOOKS
# ══════════════════════════════════════════════════════════════════════════════

def pam_hook() -> None:
    """
    Called by PAM on SSH login/logout.
    Triggered by adding to /etc/pam.d/sshd:
      session optional pam_exec.so /usr/local/bin/drift-pam-hook

    Environment variables set by PAM:
      PAM_TYPE     = open_session | close_session
      PAM_USER     = the user logging in
      PAM_RHOST    = remote host
    """
    pam_type = os.environ.get("PAM_TYPE", "")
    pam_user = os.environ.get("PAM_USER", "unknown")
    pam_host = os.environ.get("PAM_RHOST", "")

    trigger_map = {
        "open_session":  "ssh_login",
        "close_session": "ssh_logout",
    }

    trigger = trigger_map.get(pam_type, "pam_hook")
    author  = f"{pam_user}@{pam_host}" if pam_host else pam_user

    # Run in background so PAM doesn't wait for us
    try:
        pid = os.fork()
        if pid > 0:
            return   # parent returns immediately to PAM
    except OSError:
        return

    # Child: take snapshot
    try:
        cfg = DaemonConfig()
        # Exclude slow collectors for SSH hooks — we want speed
        fast_exclude = {"packages", "pip", "npm"} | cfg.exclude_collectors
        take_snapshot(trigger=trigger, author=author, exclude=fast_exclude)
    except Exception:
        pass
    finally:
        os._exit(0)
