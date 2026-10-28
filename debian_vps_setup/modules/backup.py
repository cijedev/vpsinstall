"""Config/state snapshots of files this installer owns — not disk images."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import error, heading, info, ok, warn
from debian_vps_setup.modules.system import BASE_PACKAGES
from debian_vps_setup.runner import Runner

BACKUP_NOTE = (
    "This is a config/state snapshot of files the installer manages. "
    "It is not a full disk image and cannot revert unrelated packages, "
    "mailboxes, or website content."
)

BACKUP_ROOT = "/var/lib/debian-vps-setup/backups"

STATIC_PATHS = (
    "/etc/hostname",
    "/etc/hosts",
    "/etc/fail2ban/jail.d/debian-vps-setup.conf",
    "/etc/postfix/sasl_passwd",
    "/etc/postfix/header_custom",
    "/etc/dovecot/conf.d/99-debian-vps-setup-postfix-auth.conf",
    "/etc/dovecot/conf.d/99-debian-vps-setup-mail.conf",
    "/etc/dovecot/conf.d/99-debian-vps-setup.conf",
    "/etc/aliases.d/debian-vps-setup",
    "/etc/apache2/sites-available/webmail.conf",
    "/etc/nginx/sites-available/webmail",
    "/etc/roundcube/config.inc.php.d/debian-vps-setup.inc.php",
    "/etc/php/mods-available/debian-vps-setup.ini",
    "/etc/proftpd/conf.d/debian-vps-setup-tls.conf",
    "/etc/proftpd/conf.d/debian-vps-setup-passive.conf",
    "/etc/systemd/system/vncserver.service",
    "/root/.vnc/xstartup",
    "/root/.vnc/passwd",
    "/root/vps-setup.json",
    "/root/vps-setup-report.txt",
    "/var/lib/debian-vps-setup/state.json",
    "/var/lib/snappymail/data/_data_/_default_/configs/application.ini",
    "/var/lib/cypht/.env",
)

SERVICES = (
    "fail2ban",
    "apache2",
    "nginx",
    "postfix",
    "dovecot",
    "proftpd",
    "webmin",
    "usermin",
    "1panel",
    "1panel-core",
    "vncserver",
    "mariadb",
)

PACKAGES = tuple(
    dict.fromkeys(
        [
            *BASE_PACKAGES,
            "apache2",
            "nginx",
            "php",
            "postfix",
            "dovecot-imapd",
            "proftpd-core",
            "roundcube",
            "certbot",
            "tigervnc-standalone-server",
            "xfce4",
        ]
    )
)


@dataclass
class Snapshot:
    name: str
    path: Path
    manifest: dict[str, Any]

    @property
    def created_at(self) -> str:
        return str(self.manifest.get("created_at") or self.name)

    @property
    def reason(self) -> str:
        return str(self.manifest.get("reason") or "manual")


def owned_paths(cfg: SetupConfig | None = None) -> list[str]:
    paths = list(STATIC_PATHS)
    if cfg:
        paths.extend(
            [
                f"/etc/sudoers.d/{cfg.domain_user}",
                f"/etc/apache2/sites-available/{cfg.domain}.conf",
                f"/etc/nginx/sites-available/{cfg.domain}",
                f"/var/lib/snappymail/data/_data_/_default_/domains/{cfg.domain}.ini",
                f"/var/lib/snappymail/data/_data_/_default_/domains/{cfg.domain}.json",
            ]
        )
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def backups_dir(runner: Runner) -> Path:
    return runner.resolve(BACKUP_ROOT)


def create_snapshot(
    runner: Runner,
    cfg: SetupConfig | None = None,
    *,
    reason: str = "manual",
) -> Snapshot:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir(runner) / stamp
    if dest.exists():
        stamp = stamp + "-2"
        dest = backups_dir(runner) / stamp
    files_dir = dest / "files"
    if runner.dry_run:
        info(f"[dry-run] snapshot {dest} ({reason})")
    else:
        files_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    copied = 0
    for logical in owned_paths(cfg):
        src = runner.resolve(logical)
        rel = logical.lstrip("/")
        existed = src.exists() and src.is_file()
        record = {"src": logical, "rel": rel, "existed": existed}
        records.append(record)
        if not existed:
            continue
        stored = files_dir / rel
        runner.copy_resolved(src, stored)
        copied += 1

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "domain": cfg.domain if cfg else "",
        "note": BACKUP_NOTE,
        "files": records,
        "copied": copied,
        "packages": _package_manifest(runner),
        "services": _service_manifest(runner),
    }
    text = json.dumps(manifest, indent=2) + "\n"
    if runner.dry_run:
        info(f"[dry-run] write {dest / 'manifest.json'} ({copied} files)")
    else:
        (dest / "manifest.json").write_text(text, encoding="utf-8")
        ok(f"snapshot {stamp} ({copied} files) — {BACKUP_NOTE}")
    return Snapshot(name=stamp, path=dest, manifest=manifest)


def list_snapshots(runner: Runner) -> list[Snapshot]:
    base = backups_dir(runner)
    if not base.exists():
        return []
    found: list[Snapshot] = []
    for child in sorted(base.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        data: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except json.JSONDecodeError:
                pass
        found.append(Snapshot(name=child.name, path=child, manifest=data))
    return found


def print_snapshots(runner: Runner) -> list[Snapshot]:
    snaps = list_snapshots(runner)
    heading("Config snapshots")
    info(BACKUP_NOTE)
    if not snaps:
        warn(f"no snapshots in {backups_dir(runner)}")
        return snaps
    for snap in snaps:
        domain = snap.manifest.get("domain") or "-"
        copied = snap.manifest.get("copied")
        if copied is None:
            copied = sum(1 for item in snap.manifest.get("files") or [] if item.get("existed"))
        info(f"{snap.name}  reason={snap.reason}  domain={domain}  files={copied}")
    return snaps


def restore_snapshot(runner: Runner, name: str) -> int:
    snaps = {snap.name: snap for snap in list_snapshots(runner)}
    snap = snaps.get(name)
    if snap is None:
        error(f"snapshot {name!r} not found")
        print_snapshots(runner)
        return 2
    heading(f"Restore {name}")
    info(BACKUP_NOTE)
    restored = 0
    skipped = 0
    for item in snap.manifest.get("files") or []:
        logical = item.get("src")
        rel = item.get("rel")
        if not logical or not rel or not item.get("existed"):
            skipped += 1
            continue
        stored = snap.path / "files" / rel
        if not stored.exists():
            skipped += 1
            continue
        dest = runner.resolve(str(logical))
        runner.copy_resolved(stored, dest)
        restored += 1
    ok(f"restored {restored} files from {name} ({skipped} skipped)")
    if runner.live_system:
        for unit in ("postfix", "dovecot", "apache2", "nginx", "proftpd", "fail2ban", "vncserver"):
            runner.systemctl("reload-or-restart", unit, check=False)
    return 0


def _package_manifest(runner: Runner) -> list[dict[str, str]]:
    if not runner.live_system:
        return [{"name": name, "status": "not queried"} for name in PACKAGES]
    rows: list[dict[str, str]] = []
    for name in PACKAGES:
        out = runner.output(["dpkg-query", "-W", "-f=${Status}", name])
        rows.append({"name": name, "status": out or "not installed"})
    return rows


def _service_manifest(runner: Runner) -> list[dict[str, str]]:
    if not runner.live_system:
        return [{"name": name, "active": "not queried"} for name in SERVICES]
    rows: list[dict[str, str]] = []
    for name in SERVICES:
        active = "yes" if runner.service_is_active(name) else "no"
        rows.append({"name": name, "active": active})
    return rows
