"""
drift/renderer.py

Rich terminal rendering for all drift output.
Falls back to plain text if rich is not installed.
"""
from __future__ import annotations
import sys
from typing import Optional

try:
    from rich.console import Console
    from rich.table  import Table
    from rich.panel  import Panel
    from rich.tree   import Tree
    from rich        import box
    from rich.text   import Text
    from rich.rule   import Rule
    RICH = True
except ImportError:
    RICH = False

_C: Optional[object] = None


def _c():
    global _C
    if _C is None and RICH:
        _C = Console()
    return _C


_KIND_STYLE = {
    "added":    ("green",  "+"),
    "removed":  ("red",    "-"),
    "modified": ("yellow", "~"),
}

_CATEGORY_ICON = {
    "package":       "📦",
    "service":       "⚙️ ",
    "port":          "🔌",
    "user":          "👤",
    "group":         "👥",
    "cron":          "🕐",
    "sysctl":        "🔧",
    "mount":         "💾",
    "env_var":       "🌍",
    "kernel_module": "🔩",
}


# ── log ───────────────────────────────────────────────────────────────────────

def render_log(commits: list, n: int = 20) -> None:
    if not commits:
        _plain("No commits yet. Run: drift snapshot")
        return

    if RICH:
        c = _c()
        tbl = Table(
            box=box.SIMPLE_HEAVY, border_style="cyan",
            show_lines=False, highlight=True,
        )
        tbl.add_column("Hash",      style="bold yellow",  width=13, no_wrap=True)
        tbl.add_column("When",      style="dim",          width=20, no_wrap=True)
        tbl.add_column("Changes",   style="bold",         width=8,  no_wrap=True)
        tbl.add_column("Author",    style="cyan",         width=12, no_wrap=True)
        tbl.add_column("Trigger",   style="dim",          width=11, no_wrap=True)
        tbl.add_column("Summary",                                    no_wrap=False)

        for commit in commits:
            change_col = (
                f"[red]{commit.change_count}[/red]"
                if commit.change_count > 0 else
                "[dim]0[/dim]"
            )
            tbl.add_row(
                commit.hash,
                _fmt_ts(commit.timestamp),
                change_col,
                commit.author[:12],
                commit.trigger[:11],
                commit.message[:80],
            )
        c.print(tbl)
    else:
        for commit in commits:
            print(f"{commit.hash}  {_fmt_ts(commit.timestamp)}  [{commit.change_count}]  {commit.message}")


def _fmt_ts(ts: str) -> str:
    """Make timestamp human-readable."""
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.total_seconds() < 60:
            return "just now"
        if diff.total_seconds() < 3600:
            return f"{int(diff.total_seconds() // 60)}m ago"
        if diff.total_seconds() < 86400:
            return f"{int(diff.total_seconds() // 3600)}h ago"
        if diff.days < 7:
            return f"{diff.days}d ago"
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16]


# ── diff ──────────────────────────────────────────────────────────────────────

def render_diff(result, verbose: bool = False) -> None:
    if not result.changes:
        if RICH:
            _c().print("[green]✅ No changes between snapshots[/green]")
        else:
            print("No changes.")
        return

    critical = [c for c in result.changes if c.critical]
    total    = len(result.changes)

    if RICH:
        c = _c()
        c.print()
        c.rule(f"[bold cyan]drift diff[/bold cyan]  {result.before_hash}[dim]..[/dim]{result.after_hash}")
        c.print(f"  [dim]{_fmt_ts(result.before_time)} → {_fmt_ts(result.after_time)} on {result.hostname}[/dim]")
        c.print()

        if critical:
            c.print(Panel(
                "\n".join(f"  [bold red]⚠  {ch.category}[/bold red]  {ch.name}  "
                          f"[dim]({ch.kind})[/dim]"
                          for ch in critical),
                title=f"[bold red]⚠  {len(critical)} Critical Change{'s' if len(critical)!=1 else ''}[/bold red]",
                border_style="red",
            ))
            c.print()

        for category, changes in sorted(result.by_category.items()):
            icon = _CATEGORY_ICON.get(category, "•")
            c.print(f"  {icon}  [bold]{category.upper()}[/bold]  "
                    f"[dim]({len(changes)} change{'s' if len(changes)!=1 else ''})[/dim]")

            tbl = Table(box=box.SIMPLE, show_header=False,
                        padding=(0, 1), border_style="dim")
            tbl.add_column(width=2)
            tbl.add_column(style="bold")
            tbl.add_column()
            tbl.add_column(style="dim")

            for ch in changes:
                style, sym = _KIND_STYLE.get(ch.kind, ("white", "?"))
                flag = " [bold red]⚠[/bold red]" if ch.critical else ""

                if ch.kind == "added":
                    detail = str(ch.after)
                elif ch.kind == "removed":
                    detail = str(ch.before)
                else:
                    if verbose:
                        detail = f"{ch.before} → {ch.after}"
                    else:
                        b = str(ch.before)[:40]
                        a = str(ch.after)[:40]
                        detail = f"[red]{b}[/red] → [green]{a}[/green]"

                tbl.add_row(
                    f"[{style}]{sym}[/{style}]{flag}",
                    ch.name,
                    detail,
                    "",
                )

            c.print(tbl)
            c.print()

    else:
        # Plain text fallback
        from drift.diff import diff_to_text
        print(diff_to_text(result, verbose=verbose))


# ── status ────────────────────────────────────────────────────────────────────

def render_status(snapshot, commit=None) -> None:
    if RICH:
        c = _c()
        c.print()
        c.rule("[bold cyan]drift status[/bold cyan]")
        c.print()

        tbl = Table(box=box.SIMPLE, show_header=False)
        tbl.add_column(style="bold cyan", width=18)
        tbl.add_column(style="white")

        tbl.add_row("Host",      snapshot.hostname)
        tbl.add_row("OS",        snapshot.os)
        tbl.add_row("Kernel",    snapshot.kernel)
        tbl.add_row("Snapshot",  snapshot.timestamp[:19])
        if commit:
            tbl.add_row("Last commit",  commit.hash)
            tbl.add_row("Changes",      str(commit.change_count))
            tbl.add_row("Author",       commit.author)
            tbl.add_row("Trigger",      commit.trigger)
        tbl.add_row("Packages",  str(len(snapshot.packages)))
        tbl.add_row("Services",  str(len(snapshot.services)))
        tbl.add_row("Ports",     str(len(snapshot.ports)))
        tbl.add_row("Users",     str(len(snapshot.users)))
        tbl.add_row("Cron jobs", str(len(snapshot.cron_jobs)))
        if snapshot.errors:
            tbl.add_row("[red]Errors[/red]", str(len(snapshot.errors)))

        c.print(tbl)
    else:
        print(f"Host:     {snapshot.hostname}")
        print(f"Snapshot: {snapshot.timestamp[:19]}")
        if commit:
            print(f"Commit:   {commit.hash} ({commit.change_count} changes)")


# ── blame ─────────────────────────────────────────────────────────────────────

def render_blame(blame_data: dict) -> None:
    if "error" in blame_data:
        _plain(f"Error: {blame_data['error']}")
        return

    commit   = blame_data.get("commit", {})
    sessions = blame_data.get("sessions", [])
    changes  = blame_data.get("changes", [])
    since    = blame_data.get("since", "")
    until    = blame_data.get("until", "")

    if RICH:
        c = _c()
        c.print()
        c.rule(f"[bold yellow]drift blame  {commit.get('hash','?')}[/bold yellow]")
        c.print()
        c.print(f"  [dim]Window: {since[:19]} → {until[:19]}[/dim]")
        c.print(f"  [dim]Message: {commit.get('message','?')}[/dim]")
        c.print()

        if sessions:
            c.print("[bold]SSH sessions during this window:[/bold]")
            stbl = Table(box=box.SIMPLE, padding=(0, 1))
            stbl.add_column("User",    style="cyan bold")
            stbl.add_column("From",    style="dim")
            stbl.add_column("Action",  style="yellow")
            stbl.add_column("Time",    style="dim")
            for s in sessions:
                stbl.add_row(
                    s.get("user", "?"),
                    s.get("from_ip", "?"),
                    s.get("action", "?"),
                    s.get("timestamp", "?")[:19],
                )
            c.print(stbl)
        else:
            c.print("  [dim]No SSH sessions found in this window[/dim]")
            c.print("  [dim](scheduled job or insufficient log access)[/dim]")

        c.print()
        if changes:
            c.print(f"[bold]{len(changes)} changes in this commit:[/bold]")
            for ch in changes[:20]:
                style, sym = _KIND_STYLE.get(ch.get("kind",""), ("white","?"))
                flag = " ⚠" if ch.get("critical") else ""
                c.print(f"  [{style}]{sym}[/{style}] [{ch.get('category','')}] {ch.get('name','')}{flag}")
            if len(changes) > 20:
                c.print(f"  [dim]...and {len(changes)-20} more[/dim]")
    else:
        print(f"Commit: {commit.get('hash','?')}  {commit.get('message','?')}")
        print(f"Window: {since[:19]} → {until[:19]}")
        if sessions:
            for s in sessions:
                print(f"  SSH: {s.get('user')} from {s.get('from_ip')}")
        else:
            print("  No SSH sessions found")


# ── store stats ───────────────────────────────────────────────────────────────

def render_stats(stats: dict) -> None:
    if RICH:
        c = _c()
        tbl = Table(box=box.SIMPLE_HEAVY, title="drift store stats",
                    border_style="cyan", show_header=False)
        tbl.add_column(style="bold cyan", width=20)
        tbl.add_column(style="white")
        for k, v in stats.items():
            tbl.add_row(k.replace("_", " ").title(), str(v))
        c.print(tbl)
    else:
        for k, v in stats.items():
            print(f"  {k}: {v}")


def _plain(msg: str) -> None:
    if RICH:
        _c().print(msg)
    else:
        print(msg)
