"""
drift/cli.py — main entry point

Commands:
  drift snapshot              Take a snapshot now
  drift log [n]               Show commit log (default: 20 entries)
  drift diff [hash1] [hash2]  Show changes between snapshots
  drift status                Show current server state summary
  drift blame <hash>          Show who/what caused changes in a commit
  drift show <hash>           Show full details of a snapshot
  drift search <term>         Search across all snapshots
  drift daemon start|stop|status  Manage the background daemon
  drift stats                 Show storage stats
  drift init-pam              Print PAM hook setup instructions
"""
from __future__ import annotations
import argparse
import json
import os
import sys


def _snapshot_cmd(args) -> int:
    from drift.daemon   import take_snapshot
    from drift.renderer import render_status
    from drift.storage  import head_commit, load_snapshot

    result = take_snapshot(
        trigger="manual",
        author=os.environ.get("USER", os.environ.get("LOGNAME", "unknown")),
    )

    try:
        from rich.console import Console
        c = Console()
        if result["changes"] > 0:
            c.print(f"\n[bold green]✅ Snapshot taken[/bold green]  hash=[yellow]{result['hash']}[/yellow]")
            c.print(f"   [bold]{result['changes']}[/bold] change(s): {result['message']}")
        else:
            c.print(f"\n[green]✅ Snapshot taken[/green]  hash=[yellow]{result['hash']}[/yellow]  [dim]no changes[/dim]")
        if result.get("errors"):
            c.print(f"   [yellow]⚠ {len(result['errors'])} collector error(s)[/yellow]")
    except ImportError:
        print(f"Snapshot: {result['hash']}  ({result['changes']} changes)  {result['message']}")

    return 0


def _log_cmd(args) -> int:
    from drift.storage  import read_log
    from drift.renderer import render_log

    n       = int(args.n) if hasattr(args, "n") and args.n else 20
    commits = read_log(n=n)

    if not commits:
        try:
            from rich.console import Console
            Console().print("[dim]No commits yet. Run: drift snapshot[/dim]")
        except ImportError:
            print("No commits yet. Run: drift snapshot")
        return 0

    render_log(commits, n=n)
    return 0


def _diff_cmd(args) -> int:
    from drift.storage  import read_log, load_snapshot, head_commit
    from drift.diff     import diff_snapshots
    from drift.renderer import render_diff

    commits = read_log()

    if not commits:
        print("No commits yet. Run: drift snapshot", file=sys.stderr)
        return 1

    # Resolve what to diff
    if hasattr(args, "range") and args.range:
        # drift diff HEAD~3 or drift diff abc123..def456
        spec = args.range
        if ".." in spec:
            h1, h2 = spec.split("..", 1)
        else:
            h1, h2 = spec, None
    elif hasattr(args, "hash1") and args.hash1:
        h1 = args.hash1
        h2 = getattr(args, "hash2", None)
    else:
        h1, h2 = None, None

    # Resolve special refs
    def resolve_ref(ref: str, commits: list) -> str:
        if ref is None:
            return commits[0].full_hash   # HEAD
        if ref.upper() == "HEAD":
            return commits[0].full_hash
        # HEAD~N syntax
        m = __import__("re").match(r"HEAD~(\d+)", ref, __import__("re").I)
        if m:
            idx = int(m.group(1))
            if idx < len(commits):
                return commits[idx].full_hash
        # Short hash
        for c in commits:
            if c.hash.startswith(ref) or c.full_hash.startswith(ref):
                return c.full_hash
        return ref

    hash_after  = resolve_ref(h2, commits) if h2 else commits[0].full_hash
    hash_before = resolve_ref(h1, commits) if h1 else (commits[1].full_hash if len(commits) > 1 else None)

    if not hash_before:
        print("Need at least 2 snapshots to diff.", file=sys.stderr)
        return 1

    snap_before = load_snapshot(hash_before)
    snap_after  = load_snapshot(hash_after)

    if not snap_before or not snap_after:
        print("Could not load snapshots. Check hashes.", file=sys.stderr)
        return 1

    result = diff_snapshots(snap_before, snap_after)
    render_diff(result, verbose=getattr(args, "verbose", False))
    return 0


def _status_cmd(args) -> int:
    from drift.collectors import run_all
    from drift.storage    import head_commit
    from drift.renderer   import render_status

    try:
        from rich.console import Console
        Console().print("[dim]Taking snapshot...[/dim]", end="\r")
    except ImportError:
        pass

    snapshot = run_all(
        exclude={"sysctl", "kernel_modules"}   # skip slow ones for status
    )
    commit = head_commit()
    render_status(snapshot, commit)
    return 0


def _blame_cmd(args) -> int:
    from drift.storage  import blame
    from drift.renderer import render_blame

    if not hasattr(args, "hash") or not args.hash:
        # Default to HEAD
        from drift.storage import head_commit
        c = head_commit()
        if not c:
            print("No commits yet.", file=sys.stderr)
            return 1
        hash_prefix = c.hash
    else:
        hash_prefix = args.hash

    result = blame(hash_prefix)
    render_blame(result)
    return 0


def _show_cmd(args) -> int:
    from drift.storage import load_snapshot, get_commit

    hash_prefix = args.hash

    # Try commit first, then raw snapshot
    commit   = get_commit(hash_prefix)
    snapshot = load_snapshot(hash_prefix)

    if not snapshot and commit:
        snapshot = load_snapshot(commit.full_hash)

    if not snapshot:
        print(f"Snapshot not found: {hash_prefix}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(snapshot.to_dict(), indent=2))
        return 0

    # Rich table view
    try:
        from rich.console import Console
        from rich.table   import Table
        from rich         import box
        c   = Console()
        cat_map = [
            ("Packages",       snapshot.packages,       lambda p: f"{p.name} {p.version} ({p.manager})"),
            ("Services",       snapshot.services,       lambda s: f"{s.name}  {s.state}  enabled={s.enabled}"),
            ("Open Ports",     snapshot.ports,          lambda p: f"{p.port}/{p.protocol}  {p.process}"),
            ("Users",          snapshot.users,          lambda u: f"{u.name} (uid={u.uid}) {u.shell}"),
            ("Cron Jobs",      snapshot.cron_jobs,      lambda j: f"{j.owner}: {j.schedule}  {j.command[:50]}"),
            ("Mounts",         snapshot.mounts,         lambda m: f"{m.device} → {m.mountpoint} ({m.fstype})"),
        ]
        for title, items, fmt in cat_map:
            if not items:
                continue
            tbl = Table(title=title, box=box.SIMPLE_HEAVY, border_style="cyan")
            tbl.add_column("Item", style="white")
            for item in sorted(items, key=lambda x: getattr(x, "name", "") or ""):
                tbl.add_row(fmt(item))
            c.print(tbl)
    except ImportError:
        for pkg in snapshot.packages[:20]:
            print(f"  pkg: {pkg.name} {pkg.version}")

    return 0


def _search_cmd(args) -> int:
    from drift.storage import read_log, load_snapshot

    term    = args.term.lower()
    commits = read_log()

    matches = []
    for commit in commits:
        snap = load_snapshot(commit.full_hash)
        if not snap:
            continue
        # Search across all string fields in the snapshot
        snap_json = json.dumps(snap.to_dict()).lower()
        if term in snap_json:
            matches.append((commit, snap))

    if not matches:
        try:
            from rich.console import Console
            Console().print(f"[dim]No results for '{args.term}'[/dim]")
        except ImportError:
            print(f"No results for '{args.term}'")
        return 0

    try:
        from rich.console import Console
        from rich.table   import Table
        from rich         import box
        c   = Console()
        tbl = Table(box=box.SIMPLE_HEAVY, border_style="cyan",
                    title=f"Results for '{args.term}'")
        tbl.add_column("Hash",    style="yellow",  width=12)
        tbl.add_column("When",    style="dim",      width=20)
        tbl.add_column("Where",   style="cyan",              )
        for commit, snap in matches:
            # Find which field matched
            for field_name, items in [
                ("package",  snap.packages),
                ("service",  snap.services),
                ("user",     snap.users),
                ("port",     snap.ports),
            ]:
                for item in items:
                    if term in json.dumps(item.__dict__).lower():
                        tbl.add_row(
                            commit.hash,
                            _fmt_ts(commit.timestamp),
                            f"{field_name}: {getattr(item, 'name', str(item))}",
                        )
                        break
        c.print(tbl)
    except ImportError:
        for commit, snap in matches:
            print(f"  {commit.hash}  {commit.timestamp[:19]}")

    return 0


def _fmt_ts(ts: str) -> str:
    try:
        from datetime import datetime, timezone
        dt  = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.days < 1:
            return f"{int(diff.total_seconds()//3600)}h ago"
        if diff.days < 7:
            return f"{diff.days}d ago"
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16]


def _daemon_cmd(args) -> int:
    from drift.daemon import start_daemon, stop_daemon, daemon_status

    action = args.action

    if action == "start":
        start_daemon()
    elif action == "stop":
        stop_daemon()
    elif action == "status":
        s = daemon_status()
        if s["running"]:
            print(f"drift daemon is running (pid={s['pid']})")
        else:
            print("drift daemon is not running")
    elif action == "run":
        # Foreground mode for systemd
        from drift.daemon import run_daemon
        run_daemon()
    return 0


def _export_cmd(args) -> int:
    from drift.storage import head_commit, load_snapshot, get_commit
    from drift.export  import to_ansible, to_shell, to_packages

    # Resolve snapshot
    if hasattr(args, "hash") and args.hash:
        commit = get_commit(args.hash)
        snap   = load_snapshot(args.hash) or (load_snapshot(commit.full_hash) if commit else None)
    else:
        commit = head_commit()
        snap   = load_snapshot(commit.full_hash) if commit else None

    if not snap:
        print("No snapshot found. Run: drift snapshot", file=sys.stderr)
        return 1

    fmt = getattr(args, "format", "ansible")

    if fmt == "ansible":
        content  = to_ansible(snap)
        ext      = "yml"
    elif fmt == "shell":
        content  = to_shell(snap)
        ext      = "sh"
    elif fmt == "packages":
        content  = to_packages(snap, "text")
        ext      = "txt"
    elif fmt == "packages-json":
        content  = to_packages(snap, "json")
        ext      = "json"
    elif fmt == "requirements":
        content  = to_packages(snap, "requirements")
        ext      = "txt"
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        return 1

    out_path = getattr(args, "out", None)
    show     = getattr(args, "show", False)

    if show or not out_path:
        print(content)
    else:
        from pathlib import Path
        Path(out_path).write_text(content, encoding="utf-8")
        if ext == "sh":
            import stat as _stat
            p = Path(out_path)
            p.chmod(p.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
        try:
            from rich.console import Console
            Console(stderr=True).print(
                f"[green]📄 Exported ({fmt}) →[/green] [cyan]{out_path}[/cyan]"
            )
        except ImportError:
            print(f"Exported → {out_path}")
    return 0


def _report_cmd(args) -> int:
    from drift.report import generate_report
    from pathlib import Path

    n        = getattr(args, "last", 200)
    out_path = getattr(args, "out",  "drift_report.html")

    html = generate_report(n=n)
    Path(out_path).write_text(html, encoding="utf-8")

    try:
        from rich.console import Console
        Console().print(f"[green]📊 Report saved →[/green] [cyan]{out_path}[/cyan]")
    except ImportError:
        print(f"Report saved → {out_path}")

    if getattr(args, "open_browser", False):
        import webbrowser
        webbrowser.open(f"file://{Path(out_path).resolve()}")
    return 0


def _watch_cmd(args) -> int:
    from drift.collectors.extended import watch
    interval = getattr(args, "interval", 3600)
    try:
        from rich.console import Console
        Console().print(
            f"[bold cyan]👁  drift watch[/bold cyan] — interval={interval}s\n"
            f"[dim]Watching critical paths for changes. Ctrl+C to stop.[/dim]\n"
        )
    except ImportError:
        print(f"drift watch — interval={interval}s — Ctrl+C to stop")
    watch(interval=interval)
    return 0
    from drift.storage  import store_stats
    from drift.renderer import render_stats
    render_stats(store_stats())
    return 0


def _stats_cmd(args) -> int:
    from drift.storage  import store_stats
    from drift.renderer import render_stats
    render_stats(store_stats())
    return 0


def _init_pam_cmd(args) -> int:
    try:
        from rich.console import Console
        c = Console()
        c.print("""
[bold cyan]drift PAM hook setup[/bold cyan]

This enables automatic snapshots on SSH login and logout.

[bold]1. Copy the PAM hook script:[/bold]
   sudo cp $(which drift) /usr/local/bin/drift-pam-hook

[bold]2. Add to /etc/pam.d/sshd:[/bold]
   session optional pam_exec.so /usr/local/bin/drift-pam-hook

   Or run:
   echo "session optional pam_exec.so /usr/local/bin/drift-pam-hook" \\
     | sudo tee -a /etc/pam.d/sshd

[bold]3. Test:[/bold]
   SSH into the server — a snapshot should appear in:
   drift log

[dim]The hook runs the collectors in the background and returns
immediately, so SSH login is not slowed down.[/dim]
""")
    except ImportError:
        print("Add to /etc/pam.d/sshd:")
        print("  session optional pam_exec.so /usr/local/bin/drift-pam-hook")
    return 0


def _install_cmd(args) -> int:
    """Install systemd service and PAM hook."""
    service_path = "/etc/systemd/system/drift.service"
    pam_path     = "/etc/pam.d/sshd"

    if os.geteuid() != 0:
        print("Error: drift install requires root (sudo drift install)", file=sys.stderr)
        return 1

    import shutil
    drift_bin = shutil.which("drift")
    if not drift_bin:
        print("Error: drift binary not found in PATH", file=sys.stderr)
        return 1

    # Write systemd service
    service_content = f"""[Unit]
Description=drift — server state tracker
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart={drift_bin} daemon run
Restart=always
RestartSec=30
User=root
Environment=DRIFT_INTERVAL=3600

[Install]
WantedBy=multi-user.target
"""
    with open(service_path, "w") as f:
        f.write(service_content)

    os.system("systemctl daemon-reload")
    os.system("systemctl enable drift.service")
    os.system("systemctl start drift.service")

    # PAM hook
    pam_line = f"session optional pam_exec.so {drift_bin}\n"
    with open(pam_path, "a") as f:
        f.write(pam_line)

    print(f"✅ systemd service installed → {service_path}")
    print(f"✅ PAM hook added → {pam_path}")
    print(f"   drift daemon is now running. Try: drift log")
    return 0


# ── main parser ───────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="drift",
        description="git-like server state tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  drift snapshot                    Take a snapshot right now
  drift log                         Show recent changes
  drift log 50                      Show last 50 entries
  drift diff                        Show what changed since last snapshot
  drift diff HEAD~1                 Changes between last two snapshots
  drift diff abc123 def456          Changes between specific snapshots
  drift blame abc123                Who caused changes in this commit?
  drift show abc123                 Full details of a snapshot
  drift search nginx                Find all snapshots containing 'nginx'
  drift status                      Current server state summary
  drift stats                       Storage usage
  drift daemon start|stop|status    Manage background daemon
  drift install                     Install systemd service + PAM hook (root)
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # snapshot
    p_snap = sub.add_parser("snapshot", aliases=["snap", "s"],
                             help="Take a snapshot now")

    # log
    p_log = sub.add_parser("log", aliases=["l"],
                            help="Show commit log")
    p_log.add_argument("n", nargs="?", default=20, type=int,
                       help="Number of entries (default: 20)")

    # diff
    p_diff = sub.add_parser("diff", aliases=["d"],
                             help="Show changes between snapshots")
    p_diff.add_argument("hash1", nargs="?", help="Before hash (or HEAD~N spec)")
    p_diff.add_argument("hash2", nargs="?", help="After hash (default: HEAD)")
    p_diff.add_argument("-v", "--verbose", action="store_true",
                        help="Show full before/after values")

    # status
    sub.add_parser("status", aliases=["st"], help="Current server state summary")

    # blame
    p_blame = sub.add_parser("blame", aliases=["b"],
                              help="Who/what caused changes in a commit")
    p_blame.add_argument("hash", nargs="?", help="Commit hash (default: HEAD)")

    # show
    p_show = sub.add_parser("show", help="Full details of a snapshot")
    p_show.add_argument("hash", help="Commit hash")
    p_show.add_argument("--json", action="store_true", help="Output raw JSON")

    # search
    p_search = sub.add_parser("search", help="Search across all snapshots")
    p_search.add_argument("term", help="Search term")

    # daemon
    p_daemon = sub.add_parser("daemon", help="Manage the background daemon")
    p_daemon.add_argument("action", choices=["start", "stop", "status", "run"])

    # export
    p_export = sub.add_parser("export", help="Generate Ansible/Shell from a snapshot")
    p_export.add_argument("hash",    nargs="?", help="Snapshot hash (default: HEAD)")
    p_export.add_argument("--format", default="ansible",
                          choices=["ansible", "shell", "packages", "packages-json",
                                   "requirements"],
                          help="Output format (default: ansible)")
    p_export.add_argument("--out",   metavar="PATH", help="Output file")
    p_export.add_argument("--show",  action="store_true", help="Print to stdout")

    # report
    p_report = sub.add_parser("report", help="Generate HTML change report")
    p_report.add_argument("--out",  metavar="PATH", default="drift_report.html",
                          help="Output file (default: drift_report.html)")
    p_report.add_argument("--last", metavar="N", type=int, default=200,
                          help="Number of commits to include (default: 200)")
    p_report.add_argument("--open", action="store_true", dest="open_browser",
                          help="Open in browser after generating")

    # watch
    p_watch = sub.add_parser("watch",
                              help="Watch filesystem for changes and snapshot automatically")
    p_watch.add_argument("--interval", metavar="SECS", type=int, default=3600,
                         help="Periodic snapshot interval in seconds (default: 3600)")

    # stats
    sub.add_parser("stats", help="Storage usage and stats")

    # init-pam
    sub.add_parser("init-pam", help="Print PAM hook setup instructions")

    # install
    sub.add_parser("install", help="Install systemd service + PAM hook (requires root)")

    # pam-hook (called by PAM directly)
    sub.add_parser("pam-hook", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    command_map = {
        "snapshot": _snapshot_cmd, "snap": _snapshot_cmd, "s": _snapshot_cmd,
        "log":      _log_cmd,      "l":    _log_cmd,
        "diff":     _diff_cmd,     "d":    _diff_cmd,
        "status":   _status_cmd,   "st":   _status_cmd,
        "blame":    _blame_cmd,    "b":    _blame_cmd,
        "show":     _show_cmd,
        "search":   _search_cmd,
        "export":   _export_cmd,
        "report":   _report_cmd,
        "watch":    _watch_cmd,
        "daemon":   _daemon_cmd,
        "stats":    _stats_cmd,
        "init-pam": _init_pam_cmd,
        "install":  _install_cmd,
        "pam-hook": lambda a: (__import__("drift.daemon", fromlist=["pam_hook"]).pam_hook() or 0),
    }

    fn = command_map.get(args.command)
    if not fn:
        parser.print_help()
        return 1

    try:
        return fn(args) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if os.environ.get("DRIFT_DEBUG"):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
