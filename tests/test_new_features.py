"""
tests/test_new_features.py

Tests for:
  - drift export (Ansible, Shell, packages)
  - drift report (HTML generation)
  - Extended collectors (apk, pacman, brew, cargo)
  - CLI new commands
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.models import (
    Snapshot, Package, Service, Port, User, Group,
    CronJob, SysctlParam, Mount, EnvVar,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIFT_DIR", str(tmp_path / "drift"))
    import importlib, drift.storage as st
    importlib.reload(st)
    yield tmp_path / "drift"


def _snap(**kwargs) -> Snapshot:
    s = Snapshot.new()
    s.packages = [
        Package("nginx",      "1.24", "dpkg"),
        Package("postgresql", "15.2", "dpkg"),
        Package("flask",      "3.0",  "pip"),
        Package("lodash",     "4.17", "npm"),
    ]
    s.services = [
        Service("nginx",       "active",   True),
        Service("postgresql",  "active",   True),
        Service("cron",        "active",   True),
        Service("snapd",       "inactive", False),
    ]
    s.ports    = [Port(80, "tcp", "nginx"), Port(443, "tcp", "nginx"), Port(5432, "tcp", "postgres")]
    s.users    = [User("alice", 1001, 1001, "/bin/bash", "/home/alice", ["sudo", "docker"])]
    s.cron_jobs = [CronJob("root", "0 2 * * *", "/usr/bin/backup.sh", "/etc/cron.d/backup")]
    s.sysctl   = [
        SysctlParam("net.ipv4.ip_forward",       "1"),   # non-default — should appear
        SysctlParam("vm.swappiness",              "10"),  # non-default — should appear
        SysctlParam("kernel.randomize_va_space",  "2"),   # default — should NOT appear
    ]
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT — ANSIBLE
# ══════════════════════════════════════════════════════════════════════════════

class TestExportAnsible:

    def test_generates_valid_yaml(self):
        import yaml
        from drift.export import to_ansible
        out = to_ansible(_snap())
        yml = yaml.safe_load(out.split("---", 1)[-1])
        assert isinstance(yml, list)
        assert yml[0]["hosts"] == "all"
        assert yml[0]["become"] is True

    def test_apt_packages_present(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "nginx" in out
        assert "postgresql" in out
        assert "ansible.builtin.apt" in out or "ansible.builtin.package" in out

    def test_pip_packages_present(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "flask" in out
        assert "ansible.builtin.pip" in out

    def test_npm_packages_present(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "lodash" in out
        assert "community.general.npm" in out

    def test_only_real_users_included(self):
        from drift.export import to_ansible
        s = _snap()
        # Add a system user (uid < 1000)
        s.users.append(User("nobody", 65534, 65534, "/usr/sbin/nologin", "/nonexistent"))
        out = to_ansible(s)
        assert "alice" in out
        assert "nobody" not in out

    def test_only_active_enabled_services(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "nginx" in out
        assert "postgresql" in out
        # snapd is inactive+disabled — should NOT appear
        assert "snapd" not in out

    def test_non_default_sysctl_included(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "net.ipv4.ip_forward" in out
        assert "vm.swappiness" in out

    def test_default_sysctl_excluded(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        # randomize_va_space=2 is the default — should not appear
        assert "randomize_va_space" not in out

    def test_header_has_hostname(self):
        from drift.export import to_ansible
        s = _snap()
        s.hostname = "myserver.example.com"
        out = to_ansible(s)
        assert "myserver.example.com" in out

    def test_cron_jobs_exported(self):
        from drift.export import to_ansible
        out = to_ansible(_snap())
        assert "backup.sh" in out or "ansible.builtin.cron" in out


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT — SHELL
# ══════════════════════════════════════════════════════════════════════════════

class TestExportShell:

    def test_is_bash_script(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "#!/usr/bin/env bash" in out
        assert "set -euo pipefail" in out

    def test_apt_packages_present(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "nginx" in out
        assert "apt-get install" in out

    def test_users_have_guard(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert 'id "alice"' in out
        assert "useradd" in out

    def test_services_enabled(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "systemctl enable --now nginx" in out
        assert "systemctl enable --now postgresql" in out

    def test_inactive_services_not_included(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "snapd" not in out

    def test_non_default_sysctl(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "net.ipv4.ip_forward=1" in out
        assert "sysctl -p" in out

    def test_root_check_present(self):
        from drift.export import to_shell
        out = to_shell(_snap())
        assert "EUID" in out or "root" in out.lower()


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT — PACKAGES
# ══════════════════════════════════════════════════════════════════════════════

class TestExportPackages:

    def test_text_format(self):
        from drift.export import to_packages
        out = to_packages(_snap(), "text")
        assert "nginx==1.24" in out
        assert "## dpkg" in out or "## apt" in out

    def test_json_format(self):
        from drift.export import to_packages
        out  = to_packages(_snap(), "json")
        data = json.loads(out)
        assert isinstance(data, list)
        names = {p["name"] for p in data}
        assert "nginx" in names
        assert "flask" in names

    def test_requirements_format(self):
        from drift.export import to_packages
        out = to_packages(_snap(), "requirements")
        assert "flask==3.0" in out
        # dpkg packages should not appear in requirements format
        assert "nginx" not in out

    def test_json_has_manager_field(self):
        from drift.export import to_packages
        data = json.loads(to_packages(_snap(), "json"))
        managers = {p["manager"] for p in data}
        assert "dpkg" in managers
        assert "pip"  in managers
        assert "npm"  in managers


# ══════════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ══════════════════════════════════════════════════════════════════════════════

class TestReport:

    def _seed_commits(self, n=3):
        from drift.storage import commit_snapshot
        import time
        snaps = []
        for i in range(n):
            s = Snapshot.new()
            s.packages = [Package(f"pkg{j}", "1.0", "dpkg") for j in range(i+1)]
            s.services = [Service("nginx", "active", True)]
            if i > 0:
                time.sleep(0.02)
                s.timestamp = Snapshot.new().timestamp
            commit_snapshot(s, trigger="test", author=f"user{i}")
            snaps.append(s)
        return snaps

    def test_generates_html(self):
        from drift.report import generate_report
        self._seed_commits(3)
        html = generate_report(n=10)
        assert "<!DOCTYPE html>" in html
        assert "drift" in html.lower()

    def test_contains_commit_hashes(self):
        from drift.report    import generate_report
        from drift.storage   import read_log
        self._seed_commits(2)
        html    = generate_report(n=10)
        commits = read_log(n=2)
        for c in commits:
            assert c.hash in html

    def test_stat_cards_present(self):
        from drift.report import generate_report
        self._seed_commits(3)
        html = generate_report(n=10)
        assert "Total commits" in html
        assert "Critical" in html

    def test_timeline_bar_present(self):
        from drift.report import generate_report
        self._seed_commits(2)
        html = generate_report(n=10)
        assert "tl-bar" in html
        assert "drawTimeline" in html

    def test_filter_controls_present(self):
        from drift.report import generate_report
        self._seed_commits(2)
        html = generate_report(n=10)
        assert "search" in html
        assert "cat-filter" in html

    def test_empty_log_does_not_crash(self):
        from drift.report import generate_report
        html = generate_report(n=10)
        assert "<!DOCTYPE html>" in html

    def test_detail_panel_present(self):
        from drift.report import generate_report
        self._seed_commits(2)
        html = generate_report(n=10)
        assert "detail-panel" in html
        assert "showDetail" in html


# ══════════════════════════════════════════════════════════════════════════════
# EXTENDED COLLECTORS
# ══════════════════════════════════════════════════════════════════════════════

class TestExtendedCollectors:

    def test_apk_parser(self):
        from drift.collectors.extended import collect_apk
        apk_output = (
            "nginx-1.24.0-r6 x86_64 {nginx} (BSD-2-Clause) [installed]\n"
            "curl-8.4.0-r0 x86_64 {curl} (curl) [installed]\n"
            "musl-1.2.4-r2 x86_64 {musl} (MIT) [installed]\n"
        )
        with patch("drift.collectors.extended._available", return_value=True), \
             patch("drift.collectors.extended._run", return_value=apk_output):
            pkgs, errs = collect_apk()
        assert len(pkgs) == 3
        names = {p.name for p in pkgs}
        assert "nginx" in names
        assert "curl"  in names
        assert all(p.manager == "apk" for p in pkgs)

    def test_pacman_parser(self):
        from drift.collectors.extended import collect_pacman
        pacman_output = (
            "base 3-1\n"
            "nginx 1.24.0-1\n"
            "python 3.11.6-1\n"
        )
        with patch("drift.collectors.extended._available", return_value=True), \
             patch("drift.collectors.extended._run", return_value=pacman_output):
            pkgs, errs = collect_pacman()
        assert len(pkgs) == 3
        nginx = next(p for p in pkgs if p.name == "nginx")
        assert nginx.version == "1.24.0-1"
        assert nginx.manager == "pacman"

    def test_brew_parser(self):
        from drift.collectors.extended import collect_brew
        brew_output = "nginx 1.25.0\ngit 2.42.0\ncurl 8.4.0\n"
        with patch("drift.collectors.extended._available", return_value=True), \
             patch("drift.collectors.extended._run", return_value=brew_output):
            pkgs, errs = collect_brew()
        assert any(p.name == "nginx" and p.manager == "brew" for p in pkgs)

    def test_cargo_parser(self):
        from drift.collectors.extended import collect_cargo
        cargo_output = (
            "ripgrep v13.0.0:\n"
            "    rg\n"
            "bat v0.24.0:\n"
            "    bat\n"
        )
        with patch("drift.collectors.extended._available", return_value=True), \
             patch("drift.collectors.extended._run", return_value=cargo_output):
            pkgs, errs = collect_cargo()
        assert len(pkgs) == 2
        names = {p.name for p in pkgs}
        assert "ripgrep" in names
        assert "bat"     in names
        assert all(p.manager == "cargo" for p in pkgs)

    def test_detect_distro_alpine(self, tmp_path, monkeypatch):
        from drift.collectors.extended import detect_distro
        os_release = tmp_path / "os-release"
        os_release.write_text("ID=alpine\nVERSION_ID=3.18.0\n")
        import builtins
        real_open = builtins.open
        monkeypatch.setattr("builtins.open",
            lambda f, *a, **k: real_open(os_release) if str(f) == "/etc/os-release" else real_open(f, *a, **k))
        assert detect_distro() == "alpine"

    def test_detect_distro_arch(self, tmp_path, monkeypatch):
        from drift.collectors.extended import detect_distro
        os_release = tmp_path / "os-release"
        os_release.write_text("ID=arch\nNAME=\"Arch Linux\"\n")
        import builtins
        real_open = builtins.open
        monkeypatch.setattr("builtins.open",
            lambda f, *a, **k: real_open(os_release) if str(f) == "/etc/os-release" else real_open(f, *a, **k))
        assert detect_distro() == "arch"

    def test_apk_not_available_returns_empty(self):
        from drift.collectors.extended import collect_apk
        with patch("drift.collectors.extended._available", return_value=False):
            pkgs, errs = collect_apk()
        assert pkgs == []
        assert errs == []


# ══════════════════════════════════════════════════════════════════════════════
# CLI — NEW COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

class TestCLINewCommands:

    def _seed(self):
        from drift.storage import commit_snapshot
        s = _snap()
        commit_snapshot(s, trigger="test")
        return s

    def test_export_ansible(self, capsys):
        from drift.cli import main
        self._seed()
        rc = main(["export", "--show"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "hosts: all" in out or "ansible" in out.lower()

    def test_export_shell(self, capsys):
        from drift.cli import main
        self._seed()
        rc = main(["export", "--format", "shell", "--show"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "#!/usr/bin/env bash" in out

    def test_export_packages_json(self, capsys):
        from drift.cli import main
        self._seed()
        rc = main(["export", "--format", "packages-json", "--show"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_export_no_snapshot(self):
        from drift.cli import main
        rc = main(["export", "--show"])
        assert rc == 1

    def test_export_to_file(self, tmp_path):
        from drift.cli import main
        self._seed()
        out_file = str(tmp_path / "playbook.yml")
        rc = main(["export", "--out", out_file])
        assert rc == 0
        assert Path(out_file).exists()
        content = Path(out_file).read_text()
        assert len(content) > 100

    def test_report_generates_file(self, tmp_path):
        from drift.cli import main
        self._seed()
        out_file = str(tmp_path / "report.html")
        rc = main(["report", "--out", out_file])
        assert rc == 0
        assert Path(out_file).exists()
        html = Path(out_file).read_text()
        assert "<!DOCTYPE html>" in html
        assert "drift" in html.lower()

    def test_report_empty_still_works(self, tmp_path):
        from drift.cli import main
        out_file = str(tmp_path / "empty.html")
        rc = main(["report", "--out", out_file])
        assert rc == 0
        assert Path(out_file).exists()
