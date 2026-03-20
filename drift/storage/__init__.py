"""
drift/storage/__init__.py

Content-addressable storage for snapshots.
Design is deliberately inspired by git objects:

  ~/.drift/
  ├── objects/
  │   ├── ab/
  │   │   └── cdef1234...   (full snapshot JSON, gzip compressed)
  │   └── ...
  ├── log                   (append-only JSONL commit log)
  ├── HEAD                  (hash of most recent commit)
  └── config.toml           (per-host configuration)

Snapshots are stored once and referenced by hash.
The log is an append-only file — never modified, only appended.
This means the audit trail cannot be altered without detection.
"""
from __future__ import annotations
import gzip
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from drift.models import Snapshot, Commit, DiffResult


# ── config ────────────────────────────────────────────────────────────────────

def _store_dir() -> Path:
    env = os.environ.get("DRIFT_DIR")
    if env:
        return Path(env)
    # Try system-wide first (when running as daemon), fall back to home
    system = Path("/var/lib/drift")
    if system.exists() and os.access(system, os.W_OK):
        return system
    return Path.home() / ".drift"


def store_path() -> Path:
    p = _store_dir()
    (p / "objects").mkdir(parents=True, exist_ok=True)
    return p


# ── object store ──────────────────────────────────────────────────────────────

def _obj_path(full_hash: str, base: Path) -> Path:
    """Two-char prefix like git: objects/ab/cdef1234..."""
    prefix = full_hash[:2]
    rest   = full_hash[2:]
    d = base / "objects" / prefix
    d.mkdir(parents=True, exist_ok=True)
    return d / rest


def save_snapshot(snapshot: Snapshot) -> str:
    """
    Persist a snapshot to the object store.
    Returns the full SHA-256 hash (the address of the object).
    """
    base      = store_path()
    full_hash = snapshot.digest()
    path      = _obj_path(full_hash, base)

    if path.exists():
        return full_hash   # already stored — content-addressable, no need to rewrite

    raw  = json.dumps(snapshot.to_dict(), sort_keys=True).encode("utf-8")
    with gzip.open(path, "wb") as f:
        f.write(raw)

    return full_hash


def load_snapshot(hash_prefix: str) -> Optional[Snapshot]:
    """
    Load a snapshot by hash (full or prefix — minimum 4 chars).
    Returns None if not found or multiple matches.
    """
    base = store_path()
    full_hash = _resolve_hash(hash_prefix, base)
    if not full_hash:
        return None

    path = _obj_path(full_hash, base)
    if not path.exists():
        return None

    try:
        with gzip.open(path, "rb") as f:
            raw = f.read()
        data = json.loads(raw.decode("utf-8"))
        return Snapshot.from_dict(data)
    except Exception:
        return None


def _resolve_hash(prefix: str, base: Path) -> Optional[str]:
    """Resolve a short hash prefix to a full hash. Returns None if ambiguous."""
    if len(prefix) >= 64:
        return prefix  # already full

    prefix_dir = prefix[:2] if len(prefix) >= 2 else ""
    rest_prefix = prefix[2:] if len(prefix) > 2 else ""

    candidates = []
    obj_dir = base / "objects"

    if prefix_dir:
        search_dirs = [obj_dir / prefix_dir]
    else:
        search_dirs = [d for d in obj_dir.iterdir() if d.is_dir()] if obj_dir.exists() else []

    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            full = d.name + f.name
            if full.startswith(prefix):
                candidates.append(full)

    if len(candidates) == 1:
        return candidates[0]
    return None


# ── commit log ────────────────────────────────────────────────────────────────

def _log_path() -> Path:
    return store_path() / "log"


def _head_path() -> Path:
    return store_path() / "HEAD"


def append_commit(commit: Commit) -> None:
    """Append a commit to the append-only log and update HEAD."""
    log = _log_path()
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(commit.to_dict()) + "\n")

    head = _head_path()
    head.write_text(commit.hash, encoding="utf-8")


def read_log(n: Optional[int] = None, since: Optional[str] = None) -> list[Commit]:
    """
    Read commit log entries, newest first.
    n:     maximum number of entries to return
    since: ISO timestamp — only return commits after this time
    """
    log = _log_path()
    if not log.exists():
        return []

    commits = []
    with open(log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                c    = Commit.from_dict(data)
                if since and c.timestamp < since:
                    continue
                commits.append(c)
            except (json.JSONDecodeError, TypeError):
                pass

    commits.reverse()   # newest first
    if n is not None:
        commits = commits[:n]
    return commits


def head_commit() -> Optional[Commit]:
    """Return the most recent commit."""
    commits = read_log(n=1)
    return commits[0] if commits else None


def get_commit(hash_prefix: str) -> Optional[Commit]:
    """Find a commit by hash prefix."""
    commits = read_log()
    for c in commits:
        if c.hash.startswith(hash_prefix) or c.full_hash.startswith(hash_prefix):
            return c
    return None


# ══════════════════════════════════════════════════════════════════════════════
# COMMIT CREATION
# ══════════════════════════════════════════════════════════════════════════════

def commit_snapshot(
    snapshot: Snapshot,
    trigger:  str  = "scheduled",
    author:   str  = "system",
    message:  Optional[str] = None,
) -> Commit:
    """
    Store a snapshot and create a commit entry.
    Returns the new Commit.
    """
    from drift.diff import diff_snapshots

    full_hash = save_snapshot(snapshot)
    short     = full_hash[:12]

    # Calculate changes vs previous commit
    prev = head_commit()
    changes = []
    if prev:
        prev_snap = load_snapshot(prev.full_hash)
        if prev_snap:
            result   = diff_snapshots(prev_snap, snapshot)
            changes  = result.changes

    change_count = len(changes)
    auto_message = message or (
        f"No changes" if not changes else
        _summarise_changes(changes)
    )

    commit = Commit(
        hash=short,
        full_hash=full_hash,
        timestamp=snapshot.timestamp,
        hostname=snapshot.hostname,
        message=auto_message,
        change_count=change_count,
        author=author,
        trigger=trigger,
        parent=prev.hash if prev else None,
    )

    # Only write commit if something changed (or it's the first commit)
    if not prev or changes:
        append_commit(commit)

    return commit


def _summarise_changes(changes: list) -> str:
    """Generate a short commit message from a list of Change objects."""
    cats: dict[str, dict[str, int]] = {}
    for c in changes:
        cats.setdefault(c.category, {})
        cats[c.category][c.kind] = cats[c.category].get(c.kind, 0) + 1

    parts = []
    for cat, kinds in sorted(cats.items()):
        tokens = []
        if kinds.get("added"):
            tokens.append(f"+{kinds['added']}")
        if kinds.get("removed"):
            tokens.append(f"-{kinds['removed']}")
        if kinds.get("modified"):
            tokens.append(f"~{kinds['modified']}")
        parts.append(f"{'  '.join(tokens)} {cat}")

    return ", ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# BLAME — correlate changes with SSH sessions
# ══════════════════════════════════════════════════════════════════════════════

def _parse_auth_log(since_ts: str, until_ts: str) -> list[dict]:
    """
    Parse /var/log/auth.log (or journalctl) for SSH logins
    in the window between since_ts and until_ts.
    Returns list of {user, from_ip, timestamp, action}.
    """
    sessions = []

    # Try journalctl first (systemd)
    import subprocess, shutil
    if shutil.which("journalctl"):
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ssh", "-u", "sshd",
                 "--since", since_ts[:19].replace("T", " "),
                 "--until", until_ts[:19].replace("T", " "),
                 "--no-pager", "-o", "short-iso"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                m = re.match(
                    r".*sshd.*Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)",
                    line, re.I
                )
                if m:
                    sessions.append({
                        "user":      m.group(1),
                        "from_ip":   m.group(2),
                        "timestamp": line[:25],
                        "action":    "login",
                    })
        except Exception:
            pass

    # Fallback: auth.log
    for log_path in ("/var/log/auth.log", "/var/log/secure"):
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    for line in f:
                        m = re.search(
                            r"sshd.*Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)",
                            line, re.I
                        )
                        if m:
                            sessions.append({
                                "user":    m.group(1),
                                "from_ip": m.group(2),
                                "action":  "login",
                            })
            except (IOError, PermissionError):
                pass
            break

    return sessions


def blame(hash_prefix: str) -> dict:
    """
    For a given commit hash, find what SSH sessions happened
    between the previous commit and this commit.
    Returns {"commit": Commit, "sessions": [...], "changes": [...]}
    """
    from drift.diff import diff_snapshots

    commit = get_commit(hash_prefix)
    if not commit:
        return {"error": f"Commit {hash_prefix} not found"}

    # Find parent
    commits   = read_log()
    parent    = next((c for c in commits if c.hash == commit.parent), None)
    since_ts  = parent.timestamp if parent else commit.timestamp
    until_ts  = commit.timestamp

    sessions = _parse_auth_log(since_ts, until_ts)

    # Load diff
    changes = []
    if parent:
        prev_snap = load_snapshot(parent.full_hash)
        curr_snap = load_snapshot(commit.full_hash)
        if prev_snap and curr_snap:
            diff   = diff_snapshots(prev_snap, curr_snap)
            changes = [c.to_dict() for c in diff.changes]

    return {
        "commit":   commit.to_dict(),
        "parent":   parent.to_dict() if parent else None,
        "sessions": sessions,
        "changes":  changes,
        "since":    since_ts,
        "until":    until_ts,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════════════════════════

def store_stats() -> dict:
    """Return stats about the object store."""
    base     = store_path()
    obj_dir  = base / "objects"
    log_file = _log_path()

    total_objects = 0
    total_bytes   = 0

    if obj_dir.exists():
        for d in obj_dir.iterdir():
            if d.is_dir():
                for f in d.iterdir():
                    total_objects += 1
                    total_bytes   += f.stat().st_size

    total_commits = sum(1 for _ in read_log()) if log_file.exists() else 0

    return {
        "store_dir":     str(base),
        "total_commits": total_commits,
        "total_objects": total_objects,
        "disk_usage_mb": round(total_bytes / 1024 / 1024, 2),
    }
