"""Debug lockouts and bad configs left behind by a hardened host.

Read-only except for an explicit unban of one fail2ban address. Checks SSH
settings, authorized_keys, fail2ban jails, and a few service config tests.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path

from debian_vps_setup.console import error, heading, info, ok, warn
from debian_vps_setup.runner import Runner

_JAIL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TYPOS = {
    "PasswordAuthentification": "PasswordAuthentication",
    "PubkeyAuthentification": "PubkeyAuthentication",
    "PermitRootlogin": "PermitRootLogin",
}


@dataclass
class Finding:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class JailBan:
    jail: str
    addresses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"jail": self.jail, "addresses": list(self.addresses)}


@dataclass
class DebugReport:
    findings: list[Finding] = field(default_factory=list)
    bans: list[JailBan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(item.status == "fail" for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "findings": [item.to_dict() for item in self.findings],
            "bans": [item.to_dict() for item in self.bans],
        }


def parse_sshd_settings(chunks: list[tuple[str, str]]) -> tuple[dict[str, str], list[Finding]]:
    """OpenSSH keeps the first value it sees. Later files do not override it."""
    found: dict[str, str] = {}
    notes: list[Finding] = []
    for origin, text in chunks:
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.lower().startswith("include "):
                continue
            parts = stripped.split(None, 1)
            if len(parts) != 2:
                notes.append(Finding("sshd config", "fail", f"{origin}:{line_no} is not a keyword and value"))
                continue
            key, value = parts
            canonical = _TYPOS.get(key)
            if canonical:
                notes.append(
                    Finding(
                        "sshd config",
                        "fail",
                        f"{origin}:{line_no} uses {key}; OpenSSH expects {canonical}",
                    )
                )
                key = canonical
            if key in found:
                notes.append(
                    Finding(
                        "sshd config",
                        "warn",
                        f"{origin}:{line_no} sets {key} again; the first value ({found[key]!r}) wins",
                    )
                )
                continue
            found[key] = value
    return found, notes


def parse_fail2ban_jails(text: str) -> list[str]:
    """Jail names from `fail2ban-client status`."""
    jails: list[str] = []
    for line in text.splitlines():
        if "Jail list" not in line:
            continue
        _, _, tail = line.partition(":")
        for name in tail.replace(",", " ").split():
            if name and name not in jails:
                jails.append(name)
    return jails


def parse_banned_addresses(text: str) -> list[str]:
    addresses: list[str] = []
    capture = False
    for line in text.splitlines():
        if "Banned IP list" in line:
            capture = True
            _, _, tail = line.partition(":")
            for token in tail.split():
                if token not in addresses:
                    addresses.append(token)
            continue
        if capture:
            stripped = line.strip()
            if not stripped or stripped.startswith("`") or "File list" in stripped or stripped.endswith(":"):
                if stripped.endswith(":") and "Banned" not in stripped:
                    break
                continue
            for token in stripped.split():
                if token not in addresses:
                    addresses.append(token)
    return addresses


def _read(runner: Runner, path: str) -> str:
    dest = runner.resolve(path)
    if not dest.is_file():
        return ""
    try:
        return dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _sshd_chunks(runner: Runner) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    main = _read(runner, "/etc/ssh/sshd_config")
    if main:
        chunks.append(("/etc/ssh/sshd_config", main))
    dropin = runner.resolve("/etc/ssh/sshd_config.d")
    if dropin.is_dir():
        for path in sorted(dropin.glob("*.conf")):
            chunks.append((f"/etc/ssh/sshd_config.d/{path.name}", path.read_text(encoding="utf-8", errors="replace")))
    return chunks


def _keys_present(runner: Runner, user: str = "root") -> tuple[bool, str]:
    home = "/root" if user == "root" else f"/home/{user}"
    path = f"{home}/.ssh/authorized_keys"
    text = _read(runner, path)
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    return bool(lines), path


def _mode(runner: Runner, path: str) -> int | None:
    dest = runner.resolve(path)
    if not dest.exists():
        return None
    return dest.stat().st_mode & 0o777


def assess_security(
    *,
    sshd: dict[str, str],
    sshd_notes: list[Finding],
    sshd_present: bool,
    has_root_key: bool,
    key_path: str,
    ssh_dir_mode: int | None,
    keys_mode: int | None,
    jail_config_present: bool,
    fail2ban_active: bool | None,
    bans: list[JailBan],
    config_tests: list[Finding],
) -> DebugReport:
    findings: list[Finding] = []
    if not sshd_present:
        findings.append(Finding("sshd config", "warn", "/etc/ssh/sshd_config is missing"))
    else:
        findings.extend(sshd_notes)
        pubkey = sshd.get("PubkeyAuthentication", "yes").lower()
        passwords = sshd.get("PasswordAuthentication", "yes").lower()
        root_login = sshd.get("PermitRootLogin", "prohibit-password").lower()
        port = sshd.get("Port", "22")
        if pubkey in {"no", "false", "off"}:
            findings.append(Finding("SSH keys", "fail", "PubkeyAuthentication is disabled"))
        else:
            findings.append(Finding("SSH keys", "ok", "public key authentication is allowed"))
        if passwords in {"no", "false", "off"} and not has_root_key:
            findings.append(
                Finding(
                    "SSH lockout",
                    "fail",
                    f"PasswordAuthentication is off and {key_path} has no keys",
                )
            )
        elif passwords in {"no", "false", "off"}:
            findings.append(Finding("SSH passwords", "ok", "password authentication is off and a root key is installed"))
        else:
            findings.append(Finding("SSH passwords", "warn", "password authentication is still on"))
        if root_login in {"no", "false"} and not has_root_key:
            findings.append(Finding("root SSH", "warn", "PermitRootLogin is no; use another user that has a key"))
        elif root_login in {"no", "false"}:
            findings.append(Finding("root SSH", "warn", "PermitRootLogin is no even though a root key exists"))
        else:
            findings.append(Finding("root SSH", "ok", f"PermitRootLogin {root_login}"))
        if port != "22":
            findings.append(Finding("SSH port", "warn", f"sshd listens on port {port}; clients must use that port"))
        else:
            findings.append(Finding("SSH port", "ok", "sshd port 22"))
    if ssh_dir_mode is not None and ssh_dir_mode != 0o700:
        findings.append(Finding("SSH directory", "warn", f"/root/.ssh mode is {ssh_dir_mode:o}, expected 700"))
    elif ssh_dir_mode == 0o700:
        findings.append(Finding("SSH directory", "ok", "/root/.ssh mode 700"))
    if keys_mode is not None and keys_mode not in {0o600, 0o644}:
        findings.append(Finding("authorized_keys", "warn", f"mode is {keys_mode:o}, expected 600"))
    elif keys_mode in {0o600, 0o644}:
        findings.append(Finding("authorized_keys", "ok", f"mode {keys_mode:o}"))

    if not jail_config_present:
        findings.append(Finding("fail2ban config", "warn", "no jail config under /etc/fail2ban"))
    else:
        findings.append(Finding("fail2ban config", "ok", "jail config is present"))
    if fail2ban_active is True:
        findings.append(Finding("fail2ban service", "ok", "fail2ban is active"))
    elif fail2ban_active is False:
        findings.append(Finding("fail2ban service", "fail", "jail config is present but fail2ban is not active"))
    else:
        findings.append(Finding("fail2ban service", "warn", "not queried (this is not the live host)"))

    if bans:
        listed = ", ".join(f"{ban.jail}: {', '.join(ban.addresses) or 'none'}" for ban in bans)
        banned = [ip for ban in bans for ip in ban.addresses]
        if banned:
            findings.append(Finding("fail2ban bans", "warn", listed))
        else:
            findings.append(Finding("fail2ban bans", "ok", "no addresses are banned"))
    else:
        findings.append(Finding("fail2ban bans", "ok" if fail2ban_active is not None else "warn", "no ban list was collected"))

    findings.extend(config_tests)
    return DebugReport(findings=findings, bans=bans)


def _jail_files(runner: Runner) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in ("/etc/fail2ban/jail.local", "/etc/fail2ban/jail.conf"):
        text = _read(runner, path)
        if text:
            found.append((path, text))
    dropin = runner.resolve("/etc/fail2ban/jail.d")
    if dropin.is_dir():
        for path in sorted(dropin.glob("*.conf")):
            found.append((f"/etc/fail2ban/jail.d/{path.name}", path.read_text(encoding="utf-8", errors="replace")))
    return found


def _config_tests(runner: Runner, sshd: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    if not runner.live_system:
        port = sshd.get("Port")
        for _path, text in _jail_files(runner):
            if port and port != "22" and "port" in text.lower() and port not in text and "ssh" in text.lower():
                findings.append(
                    Finding(
                        "fail2ban ssh port",
                        "warn",
                        f"sshd Port is {port} but the jail file may still watch port 22",
                    )
                )
                break
        return findings

    checks = [
        ("sshd -t", ["sshd", "-t"]),
        ("nginx -t", ["nginx", "-t"]),
        ("apache2ctl configtest", ["apache2ctl", "configtest"]),
        ("postfix check", ["postfix", "check"]),
    ]
    for name, cmd in checks:
        binary = cmd[0]
        if binary == "apache2ctl":
            present = runner.which("apache2ctl") or runner.which("apachectl")
        else:
            present = runner.which(binary)
        if not present:
            continue
        result = runner.run(cmd, check=False)
        detail = (result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {result.returncode}"
        if result.returncode == 0:
            findings.append(Finding(name, "ok", tail or "ok"))
        else:
            findings.append(Finding(name, "fail", tail))
    return findings


def _collect_bans(runner: Runner) -> tuple[bool | None, list[JailBan]]:
    if not runner.live_system:
        return None, []
    if not runner.which("fail2ban-client"):
        return None, []
    active = runner.service_is_active("fail2ban")
    status = runner.run(["fail2ban-client", "status"], check=False)
    if status.returncode != 0:
        return active, []
    bans: list[JailBan] = []
    for jail in parse_fail2ban_jails(status.stdout or ""):
        detail = runner.run(["fail2ban-client", "status", jail], check=False)
        bans.append(JailBan(jail, parse_banned_addresses(detail.stdout or "")))
    return active, bans


def debug_security(runner: Runner) -> DebugReport:
    chunks = _sshd_chunks(runner)
    sshd, notes = parse_sshd_settings(chunks)
    has_key, key_path = _keys_present(runner)
    jail_files = _jail_files(runner)
    active, bans = _collect_bans(runner)
    report = assess_security(
        sshd=sshd,
        sshd_notes=notes,
        sshd_present=bool(chunks),
        has_root_key=has_key,
        key_path=key_path,
        ssh_dir_mode=_mode(runner, "/root/.ssh"),
        keys_mode=_mode(runner, key_path),
        jail_config_present=bool(jail_files),
        fail2ban_active=active,
        bans=bans,
        config_tests=_config_tests(runner, sshd),
    )
    return report


def print_report(report: DebugReport) -> None:
    heading("Security debug")
    info("Looks for fail2ban bans, SSH lockouts, and config mistakes. Nothing is changed.")
    for item in report.findings:
        line = f"{item.name} — {item.detail}"
        if item.status == "ok":
            ok(line)
        elif item.status == "warn":
            warn(line)
        else:
            error(line)
    if report.bans:
        heading("fail2ban bans")
        for ban in report.bans:
            if ban.addresses:
                warn(f"{ban.jail}: {', '.join(ban.addresses)}")
            else:
                ok(f"{ban.jail}: none")


def valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return True


def unban_address(runner: Runner, address: str, jail: str = "sshd") -> Finding:
    address = address.strip()
    jail = jail.strip() or "sshd"
    if not valid_ip(address):
        return Finding("unban", "fail", "that is not an IP address")
    if not _JAIL_RE.match(jail):
        return Finding("unban", "fail", "jail name is not valid")
    if not runner.live_system:
        info(f"[not live] would run: fail2ban-client set {jail} unbanip {address}")
        return Finding("unban", "ok", f"would unban {address} from {jail}")
    if not runner.which("fail2ban-client"):
        return Finding("unban", "fail", "fail2ban-client is not installed")
    result = runner.run(["fail2ban-client", "set", jail, "unbanip", address], check=False)
    if result.returncode == 0:
        ok(f"unbanned {address} from {jail}")
        return Finding("unban", "ok", f"unbanned {address} from {jail}")
    detail = (result.stdout or "").strip() or f"exit {result.returncode}"
    error(detail)
    return Finding("unban", "fail", detail)
