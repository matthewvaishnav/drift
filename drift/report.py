"""
drift/report.py

Generates a self-contained HTML report showing:
  - A timeline of all commits with change counts
  - Filterable change table
  - Per-category breakdown charts
  - Critical change highlights

No external dependencies — pure HTML + vanilla JS.

Usage:
    drift report                    # generate report.html in current dir
    drift report --out /tmp/r.html  # custom output path
    drift report --last 30          # only last 30 commits
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from drift.storage import read_log, load_snapshot
from drift.diff    import diff_snapshots

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
    --bg:      #0d1117; --surface: #161b22; --border: #30363d;
    --text:    #c9d1d9; --muted:   #8b949e; --accent: #58a6ff;
    --green:   #3fb950; --yellow:  #d29922; --red:    #f85149;
    --orange:  #e3b341; --purple:  #bc8cff;
}
body { background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; padding:2rem; }
h1 { color:var(--accent); font-size:1.6rem; margin-bottom:.25rem; }
.subtitle { color:var(--muted); font-size:.9rem; margin-bottom:2rem; }
.stats { display:flex; gap:1.5rem; margin-bottom:2rem; flex-wrap:wrap; }
.stat { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:1rem 1.5rem; min-width:130px; }
.stat .val { font-size:2rem; font-weight:700; }
.stat .lbl { font-size:.75rem; color:var(--muted); text-transform:uppercase; }
.stat.c-blue   .val { color:var(--accent); }
.stat.c-green  .val { color:var(--green); }
.stat.c-red    .val { color:var(--red); }
.stat.c-yellow .val { color:var(--yellow); }
.timeline { margin-bottom:2rem; }
.tl-header { font-size:.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; margin-bottom:.5rem; font-weight:600; }
.tl-bar { display:flex; height:48px; gap:2px; align-items:flex-end; overflow:hidden; border-radius:6px; background:var(--surface); padding:.5rem; }
.tl-col { flex:1; min-width:4px; border-radius:2px 2px 0 0; cursor:pointer; transition:opacity .15s; }
.tl-col:hover { opacity:.75; }
.filter-bar { display:flex; gap:.75rem; margin-bottom:1.25rem; flex-wrap:wrap; align-items:center; }
.filter-bar input, .filter-bar select {
    background:var(--surface); border:1px solid var(--border);
    color:var(--text); padding:.35rem .75rem; border-radius:6px; font-size:.85rem;
}
.filter-bar input { min-width:200px; }
table { width:100%; border-collapse:collapse; font-size:.875rem; }
thead th {
    background:var(--surface); color:var(--muted); text-align:left;
    padding:.6rem 1rem; border-bottom:1px solid var(--border);
    font-size:.73rem; font-weight:600; text-transform:uppercase; letter-spacing:.05em;
}
tbody tr { border-bottom:1px solid #21262d; }
tbody tr:hover { background:var(--surface); }
td { padding:.6rem 1rem; vertical-align:middle; }
.hash { font-family:'Consolas','Monaco',monospace; color:var(--yellow); font-size:.82rem; cursor:pointer; }
.hash:hover { text-decoration:underline; }
.time { color:var(--muted); font-size:.8rem; white-space:nowrap; }
.author { color:var(--accent); font-size:.82rem; }
.badge { display:inline-block; padding:.15rem .45rem; border-radius:999px; font-size:.72rem; font-weight:700; text-transform:uppercase; }
.b-added    { background:#0a2d16; color:var(--green);  border:1px solid #3fb95044; }
.b-removed  { background:#3d1a1a; color:var(--red);    border:1px solid #f8514944; }
.b-modified { background:#2d2a0a; color:var(--yellow); border:1px solid #d2992244; }
.b-critical { background:#3d1a1a; color:var(--red);    border:1px solid #f85149; font-weight:800; }
.b-trigger  { background:#21262d; color:var(--muted);  border:1px solid var(--border); }
.changes-cell { font-size:.8rem; color:var(--muted); max-width:300px; }
.no-changes { color:var(--muted); font-style:italic; font-size:.8rem; }
#detail-panel {
    position:fixed; top:0; right:0; width:480px; height:100vh;
    background:var(--surface); border-left:1px solid var(--border);
    overflow-y:auto; padding:1.5rem; transform:translateX(100%);
    transition:transform .25s; z-index:100;
}
#detail-panel.open { transform:translateX(0); }
#detail-close { position:absolute; top:1rem; right:1rem; cursor:pointer; color:var(--muted); font-size:1.2rem; }
#detail-close:hover { color:var(--text); }
.detail-hash { font-family:monospace; color:var(--yellow); font-size:.85rem; margin-bottom:.5rem; }
.detail-section { margin-top:1rem; }
.detail-section h3 { font-size:.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; margin-bottom:.5rem; }
.change-row { display:flex; gap:.5rem; padding:.3rem 0; font-size:.83rem; border-bottom:1px solid #21262d; align-items:flex-start; }
.change-sym { width:16px; text-align:center; flex-shrink:0; }
.change-cat { color:var(--muted); font-size:.75rem; min-width:80px; }
.change-name { color:var(--text); font-weight:600; }
.change-detail { color:var(--muted); font-size:.77rem; word-break:break-all; }
"""

_JS = """
const commits = COMMITS_DATA;
const changes = CHANGES_DATA;

function fmtTime(ts) {
    const d = new Date(ts);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60)   return 'just now';
    if (diff < 3600) return Math.floor(diff/60) + 'm ago';
    if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
    return d.toLocaleDateString();
}

// Draw timeline
function drawTimeline() {
    const bar = document.getElementById('tl-bar');
    if (!commits.length) return;
    const max = Math.max(...commits.map(c => c.change_count), 1);
    commits.slice().reverse().forEach(c => {
        const col = document.createElement('div');
        col.className = 'tl-col';
        const h = Math.max(4, (c.change_count / max) * 36);
        col.style.height = h + 'px';
        const color = c.change_count === 0 ? '#30363d'
                    : c.change_count > 10  ? 'var(--red)'
                    : c.change_count > 3   ? 'var(--yellow)'
                    : 'var(--green)';
        col.style.background = color;
        col.title = `${c.hash}  ${c.change_count} changes  ${fmtTime(c.timestamp)}`;
        col.onclick = () => showDetail(c.hash);
        bar.appendChild(col);
    });
}

function filter() {
    const q    = document.getElementById('search').value.toLowerCase();
    const cat  = document.getElementById('cat-filter').value;
    const kind = document.getElementById('kind-filter').value;
    const rows = document.querySelectorAll('tbody tr[data-hash]');
    rows.forEach(row => {
        const txt   = row.textContent.toLowerCase();
        const rCat  = row.dataset.category || '';
        const rKind = row.dataset.kind || '';
        row.style.display = (
            (!q    || txt.includes(q))   &&
            (!cat  || rCat  === cat)     &&
            (!kind || rKind === kind)
        ) ? '' : 'none';
    });
}

function showDetail(hash) {
    const panel  = document.getElementById('detail-panel');
    const commit = commits.find(c => c.hash === hash || c.full_hash.startsWith(hash));
    if (!commit) return;

    document.getElementById('detail-hash').textContent = commit.hash;
    document.getElementById('detail-time').textContent = fmtTime(commit.timestamp);
    document.getElementById('detail-author').textContent = commit.author;
    document.getElementById('detail-trigger').textContent = commit.trigger;
    document.getElementById('detail-msg').textContent = commit.message;

    const body = document.getElementById('detail-changes');
    body.innerHTML = '';
    const chs = changes[hash] || [];
    if (!chs.length) {
        body.innerHTML = '<div class="no-changes">No changes in this commit</div>';
    } else {
        chs.forEach(ch => {
            const row = document.createElement('div');
            row.className = 'change-row';
            const sym = ch.kind === 'added' ? '<span style="color:var(--green)">+</span>'
                      : ch.kind === 'removed' ? '<span style="color:var(--red)">-</span>'
                      : '<span style="color:var(--yellow)">~</span>';
            const warn = ch.critical ? ' <span style="color:var(--red)">⚠</span>' : '';
            const detail = ch.kind === 'added'   ? ch.after
                         : ch.kind === 'removed' ? ch.before
                         : `${ch.before} → ${ch.after}`;
            row.innerHTML = `
                <span class="change-sym">${sym}${warn}</span>
                <span class="change-cat">${ch.category}</span>
                <div><div class="change-name">${ch.name}</div>
                <div class="change-detail">${detail || ''}</div></div>`;
            body.appendChild(row);
        });
    }
    panel.classList.add('open');
}

document.addEventListener('DOMContentLoaded', () => {
    drawTimeline();
    document.getElementById('search').addEventListener('input', filter);
    document.getElementById('cat-filter').addEventListener('change', filter);
    document.getElementById('kind-filter').addEventListener('change', filter);
    document.getElementById('detail-close').addEventListener('click', () => {
        document.getElementById('detail-panel').classList.remove('open');
    });
});
"""


def generate_report(n: int = 200) -> str:
    """Build a full HTML audit report from the commit log."""
    commits  = read_log(n=n)
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build change data per commit (load diffs)
    changes_by_hash: dict[str, list[dict]] = {}
    total_changes   = 0
    critical_total  = 0
    categories_seen: set[str] = set()

    prev_snap = None
    for commit in reversed(commits):   # oldest first to build diffs
        snap = load_snapshot(commit.full_hash)
        if snap and prev_snap:
            diff = diff_snapshots(prev_snap, snap)
            ch_list = []
            for ch in diff.changes:
                d = ch.to_dict()
                # Truncate long values for the report
                if d.get("before") and len(str(d["before"])) > 80:
                    d["before"] = str(d["before"])[:80] + "..."
                if d.get("after") and len(str(d["after"])) > 80:
                    d["after"] = str(d["after"])[:80] + "..."
                ch_list.append(d)
                categories_seen.add(ch.category)
                if ch.critical:
                    critical_total += 1
            changes_by_hash[commit.hash] = ch_list
            total_changes += len(ch_list)
        prev_snap = snap

    # Stats
    total_commits   = len(commits)
    with_changes    = sum(1 for c in commits if c.change_count > 0)
    hostnames       = sorted({c.hostname for c in commits})

    # Build flat rows for the table (one row per change, grouped by commit)
    table_rows = []
    for commit in commits:
        chs = changes_by_hash.get(commit.hash, [])
        if not chs:
            table_rows.append({
                "hash":      commit.hash,
                "timestamp": commit.timestamp,
                "author":    commit.author,
                "trigger":   commit.trigger,
                "category":  "",
                "kind":      "",
                "name":      "",
                "before":    "",
                "after":     "",
                "critical":  False,
                "message":   commit.message,
                "no_changes": True,
            })
        else:
            for ch in chs:
                table_rows.append({
                    **ch,
                    "hash":      commit.hash,
                    "timestamp": commit.timestamp,
                    "author":    commit.author,
                    "trigger":   commit.trigger,
                    "message":   commit.message,
                    "no_changes": False,
                })

    # Build category filter options
    cat_options = "\n".join(
        f'<option value="{c}">{c}</option>'
        for c in sorted(categories_seen)
    )

    # Embed data
    commits_json = json.dumps([c.to_dict() for c in commits])
    changes_json = json.dumps(changes_by_hash)

    # Table rows HTML
    rows_html = []
    for row in table_rows:
        if row.get("no_changes"):
            rows_html.append(f"""
            <tr data-hash="{row['hash']}" data-category="" data-kind="">
              <td><span class="hash" onclick="showDetail('{row['hash']}')">{row['hash']}</span></td>
              <td class="time">{_fmt(row['timestamp'])}</td>
              <td class="author">{row['author']}</td>
              <td><span class="badge b-trigger">{row['trigger']}</span></td>
              <td></td>
              <td></td>
              <td class="no-changes">{row['message']}</td>
              <td></td>
            </tr>""")
        else:
            kind_cls  = {"added":"b-added","removed":"b-removed","modified":"b-modified"}.get(row.get("kind",""),"")
            crit_flag = '<span class="badge b-critical">⚠ critical</span>' if row.get("critical") else ""
            detail    = row.get("after") or row.get("before") or ""
            rows_html.append(f"""
            <tr data-hash="{row['hash']}" data-category="{row.get('category','')}" data-kind="{row.get('kind','')}">
              <td><span class="hash" onclick="showDetail('{row['hash']}')">{row['hash']}</span></td>
              <td class="time">{_fmt(row['timestamp'])}</td>
              <td class="author">{row['author']}</td>
              <td><span class="badge b-trigger">{row['trigger']}</span></td>
              <td><code style="font-size:.8rem">{row.get('category','')}</code></td>
              <td><span class="badge {kind_cls}">{row.get('kind','')}</span> {crit_flag}</td>
              <td style="max-width:220px;font-size:.82rem">{row.get('name','')}</td>
              <td class="changes-cell" title="{detail}">{str(detail)[:60]}</td>
            </tr>""")

    js = _JS.replace("COMMITS_DATA", commits_json).replace("CHANGES_DATA", changes_json)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>drift — change report</title>
  <style>{_CSS}</style>
</head>
<body>
  <h1>🔍 drift — Change Report</h1>
  <p class="subtitle">Generated {now} · Last {total_commits} commits · {", ".join(hostnames) or "unknown host"}</p>

  <div class="stats">
    <div class="stat c-blue">  <div class="val">{total_commits}</div>  <div class="lbl">Total commits</div></div>
    <div class="stat c-yellow"><div class="val">{with_changes}</div>   <div class="lbl">With changes</div></div>
    <div class="stat c-green"> <div class="val">{total_changes}</div>  <div class="lbl">Total changes</div></div>
    <div class="stat c-red">   <div class="val">{critical_total}</div> <div class="lbl">Critical</div></div>
  </div>

  <div class="timeline">
    <div class="tl-header">Change frequency timeline (newest right)</div>
    <div class="tl-bar" id="tl-bar"></div>
  </div>

  <div class="filter-bar">
    <input id="search"      type="text"   placeholder="Search hashes, names, authors...">
    <select id="cat-filter"><option value="">All categories</option>{cat_options}</select>
    <select id="kind-filter">
      <option value="">All changes</option>
      <option value="added">Added</option>
      <option value="removed">Removed</option>
      <option value="modified">Modified</option>
    </select>
  </div>

  <table>
    <thead>
      <tr>
        <th>Hash</th><th>When</th><th>Author</th><th>Trigger</th>
        <th>Category</th><th>Kind</th><th>Name</th><th>Value</th>
      </tr>
    </thead>
    <tbody>{"".join(rows_html)}</tbody>
  </table>

  <!-- Detail side panel -->
  <div id="detail-panel">
    <span id="detail-close">✕</span>
    <div class="detail-hash" id="detail-hash"></div>
    <div class="detail-section">
      <table style="width:100%;font-size:.82rem">
        <tr><td style="color:var(--muted);width:80px">Time</td>   <td id="detail-time"></td></tr>
        <tr><td style="color:var(--muted)">Author</td>  <td id="detail-author"></td></tr>
        <tr><td style="color:var(--muted)">Trigger</td> <td id="detail-trigger"></td></tr>
        <tr><td style="color:var(--muted)">Message</td> <td id="detail-msg" style="color:var(--muted);font-size:.8rem"></td></tr>
      </table>
    </div>
    <div class="detail-section">
      <h3>Changes</h3>
      <div id="detail-changes"></div>
    </div>
  </div>

  <script>{js}</script>
</body>
</html>"""


def _fmt(ts: str) -> str:
    try:
        dt  = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 3600:  return f"{int(diff//60)}m ago"
        if diff < 86400: return f"{int(diff//3600)}h ago"
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ts[:16]
