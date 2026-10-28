"""Post-install checks for every service this program configures."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import error, heading, ok, warn
from debian_vps_setup.modules.mail import SASL_PASSWD, postfix_settings
from debian_vps_setup.runner import Runner

COMPONENTS = (
    "system",
    "panel",
    "domain",
    "web",
    "mail",
    "ftp",
    "ssl",
    "webmail",
    "vnc",
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    component: str = ""


def _pass(name: str, detail: str = "", component: str = "") -> Check:
    ok(f"{name}" + (f" — {detail}" if detail else ""))
    return Check(name, True, detail, component)


def _fail(name: str, detail: str, component: str = "") -> Check:
    error(f"{name} — {detail}")
    return Check(name, False, detail, component)


def _skip(name: str, detail: str, component: str = "") -> Check:
    warn(f"{name} — skipped: {detail}")
    return Check(name, True, detail, component)


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def canonicalize_components(raw: Sequence[str] | None) -> list[str]:
    if not raw:
        return list(COMPONENTS)
    wanted: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = item.strip().lower()
        if not key or key == "all":
            return list(COMPONENTS)
        if key not in COMPONENTS:
            raise ValueError(f"unknown component {item!r}; choose from: {', '.join(COMPONENTS)}")
        if key not in seen:
            seen.add(key)
            wanted.append(key)
    return wanted or list(COMPONENTS)


def run_checks(
    cfg: SetupConfig,
    runner: Runner,
    components: Sequence[str] | None = None,
) -> list[Check]:
    heading("Configuration tests")
    wanted = set(canonicalize_components(components))
    checks: list[Check] = []
    if "system" in wanted:
        checks.extend(_check_system(cfg, runner))
    if "panel" in wanted:
        checks.append(_check_control_panel(cfg, runner))
    if "domain" in wanted:
        checks.extend(_check_domain(cfg, runner))
    if "web" in wanted:
        checks.append(_check_binary(runner, "web server", ["apache2", "httpd", "nginx"], "web"))
        checks.append(_check_php(runner))
    if "mail" in wanted:
        checks.append(_check_service(runner, "postfix", "mail"))
        checks.append(_check_mailbaby(cfg, runner))
        checks.append(_check_dovecot(runner))
        checks.append(_check_smtp_auth(runner))
        if cfg.send_test_mail_to:
            checks.append(_send_test_mail(cfg, runner))
    if "ftp" in wanted:
        checks.append(_check_service(runner, "proftpd", "ftp"))
    if "ssl" in wanted:
        checks.append(_check_ssl(cfg, runner))
    if "webmail" in wanted:
        checks.append(_check_webmail(cfg, runner))
    if "vnc" in wanted:
        if cfg.vnc.enabled:
            checks.append(_check_vnc(cfg, runner))
        else:
            checks.append(_skip("VNC", "disabled in config", "vnc"))
    return checks


def _check_system(cfg: SetupConfig, runner: Runner) -> list[Check]:
    checks: list[Check] = []
    hostname_path = runner.resolve("/etc/hostname")
    if runner.dry_run:
        checks.append(_pass("hostname", cfg.hostname, "system"))
    elif hostname_path.exists() and cfg.hostname in hostname_path.read_text(encoding="utf-8"):
        checks.append(_pass("hostname", cfg.hostname, "system"))
    else:
        checks.append(_fail("hostname", f"expected {cfg.hostname} in /etc/hostname", "system"))

    jail = "/etc/fail2ban/jail.d/debian-vps-setup.conf"
    if runner.dry_run:
        checks.append(_pass("fail2ban", "dry-run", "system"))
    elif runner.exists(jail):
        if runner.live_system and not runner.service_is_active("fail2ban"):
            checks.append(_fail("fail2ban", "jail config present but service is not active", "system"))
        else:
            checks.append(_pass("fail2ban", "config present", "system"))
    elif runner.live_system and runner.service_is_active("fail2ban"):
        checks.append(_pass("fail2ban", "active", "system"))
    else:
        checks.append(_fail("fail2ban", "jail config missing", "system"))
    return checks


def _check_domain(cfg: SetupConfig, runner: Runner) -> list[Check]:
    if runner.dry_run or runner.exists(cfg.document_root):
        return [_pass("document root", cfg.document_root, "domain")]
    return [_fail("document root", f"{cfg.document_root} is missing", "domain")]


def _check_binary(runner: Runner, name: str, binaries: list[str], component: str) -> Check:
    for binary in binaries:
        if runner.which(binary) or runner.exists(f"/usr/sbin/{binary}"):
            return _pass(name, binary, component)
    if runner.dry_run:
        return _pass(name, "dry-run", component)
    return _fail(name, f"none of {', '.join(binaries)} found", component)


def _check_php(runner: Runner) -> Check:
    if runner.dry_run:
        return _pass("PHP", "dry-run", "web")
    if not runner.which("php"):
        return _fail("PHP", "php not on PATH", "web")
    version = runner.output(["php", "-r", "echo PHP_VERSION;"])
    modules = runner.output(["php", "-m"]).lower()
    missing = [ext for ext in ("mysqli", "xml", "mbstring", "curl") if ext not in modules]
    if missing:
        return _fail("PHP", f"version {version}, missing modules: {', '.join(missing)}", "web")
    return _pass("PHP", version, "web")


def _check_listening(runner: Runner, name: str, port: int, component: str) -> Check:
    if runner.dry_run:
        return _pass(name, f"port {port} (dry-run)", component)
    if runner.service_is_active(name.lower()) or port_open("127.0.0.1", port) or runner.listening(port):
        return _pass(name, f"port {port}", component)
    return _fail(name, f"nothing listening on {port}", component)


def _check_service(runner: Runner, unit: str, component: str) -> Check:
    if runner.dry_run:
        return _pass(unit, "dry-run", component)
    if runner.service_is_active(unit):
        return _pass(unit, "active", component)
    alt = unit + ".service"
    if runner.service_is_active(alt):
        return _pass(unit, "active", component)
    return _fail(unit, "service is not active", component)


def _check_mailbaby(cfg: SetupConfig, runner: Runner) -> Check:
    if not cfg.mailbaby_enabled:
        return _skip("Mailbaby relay", "no Mailbaby credentials configured", "mail")
    if runner.dry_run:
        return _pass("Mailbaby relay", cfg.mailbaby.relayhost(), "mail")
    if not runner.exists(SASL_PASSWD):
        return _fail("Mailbaby relay", f"{SASL_PASSWD} missing", "mail")
    relay = runner.output(["postconf", "-h", "relayhost"])
    expected = postfix_settings(cfg)["relayhost"]
    auth = runner.output(["postconf", "-h", "smtp_sasl_auth_enable"])
    if expected.split()[0] not in relay.replace("[", "").replace("]", "") and expected not in relay:
        if cfg.mailbaby.host not in relay:
            return _fail("Mailbaby relay", f"relayhost is {relay!r}, expected {expected!r}", "mail")
    if auth.strip() != "yes":
        return _fail("Mailbaby relay", "smtp_sasl_auth_enable is not yes", "mail")
    if not (runner.exists(SASL_PASSWD + ".db") or runner.exists(SASL_PASSWD + ".lmdb")):
        return _fail("Mailbaby relay", "sasl_passwd map missing (postmap not run)", "mail")
    return _pass("Mailbaby relay", relay, "mail")


def _check_smtp_auth(runner: Runner) -> Check:
    if runner.dry_run:
        return _pass("SMTP AUTH", "dry-run", "mail")
    sasl_type = runner.output(["postconf", "-h", "smtpd_sasl_type"]).strip()
    sasl_path = runner.output(["postconf", "-h", "smtpd_sasl_path"]).strip()
    sock = "/var/spool/postfix/private/auth"
    if sasl_type != "dovecot":
        return _fail("SMTP AUTH", f"smtpd_sasl_type is {sasl_type!r}, expected dovecot", "mail")
    if not runner.exists(sock):
        return _fail("SMTP AUTH", f"{sock} missing — Roundcube cannot authenticate", "mail")
    return _pass("SMTP AUTH", f"dovecot {sasl_path}", "mail")


def _check_dovecot(runner: Runner) -> Check:
    if runner.dry_run:
        return _pass("Dovecot", "dry-run", "mail")
    if runner.live_system and not runner.service_is_active("dovecot"):
        return _fail("Dovecot", "service is not active", "mail")
    if runner.live_system and not (port_open("127.0.0.1", 993) or port_open("127.0.0.1", 143)):
        return _fail("Dovecot", "IMAP ports 143/993 are closed", "mail")
    layout = runner.resolve("/etc/dovecot/conf.d/99-debian-vps-setup-mail.conf")
    if layout.exists():
        driver = layout.read_text(encoding="utf-8", errors="replace")
        if "maildir" in driver.lower():
            return _pass("Dovecot", "IMAP up, maildir ~/Maildir", "mail")
    if not runner.live_system:
        return _fail("Dovecot", "maildir layout config missing", "mail")
    driver = runner.output(["doveconf", "-h", "mail_driver"])
    if "maildir" not in driver.lower():
        return _fail(
            "Dovecot",
            f"mail_driver is {driver!r}, expected maildir (~/Maildir, same as Usermin)",
            "mail",
        )
    return _pass("Dovecot", "IMAP up, maildir ~/Maildir", "mail")


def _check_ssl(cfg: SetupConfig, runner: Runner) -> Check:
    if not cfg.ssl.enabled:
        return _skip("SSL", "disabled", "ssl")
    if runner.dry_run:
        return _pass("SSL", "dry-run", "ssl")
    live = f"/etc/letsencrypt/live/{cfg.domain}/fullchain.pem"
    if runner.exists(live):
        return _pass("SSL", live, "ssl")
    if runner.which("virtualmin"):
        out = runner.output(["virtualmin", "list-certs", "--domain", cfg.domain])
        if out:
            return _pass("SSL", "Virtualmin certificate present", "ssl")
    return _fail(
        "SSL",
        "no Let's Encrypt certificate found — point DNS at this VPS and re-run: "
        "cije-vps-installer run --only ssl",
        "ssl",
    )


def _check_webmail(cfg: SetupConfig, runner: Runner) -> Check:
    if runner.dry_run:
        return _pass("webmail", cfg.webmail, "webmail")
    if cfg.webmail == "usermin":
        if port_open("127.0.0.1", 20000) or runner.service_is_active("usermin") or runner.exists("/etc/usermin"):
            return _pass("webmail", "Usermin :20000", "webmail")
        return _fail("webmail", "Usermin is not running", "webmail")
    if cfg.webmail == "snappymail":
        paths = [
            f"{SNAPPYMAIL_INDEX}",
            f"{cfg.document_root}/snappymail/index.php",
        ]
        if any(runner.exists(path) for path in paths):
            return _pass("webmail", "SnappyMail files present", "webmail")
        return _fail("webmail", "SnappyMail not found", "webmail")
    if cfg.webmail == "cypht":
        paths = [
            f"{CYPHT_INDEX}",
            f"{cfg.document_root}/cypht/index.php",
        ]
        if any(runner.exists(path) for path in paths):
            return _pass("webmail", "Cypht files present", "webmail")
        return _fail("webmail", "Cypht not found", "webmail")
    paths = [
        "/usr/share/roundcube/index.php",
        "/var/lib/roundcube/index.php",
        "/var/lib/roundcube/public_html/index.php",
    ]
    if any(runner.exists(path) for path in paths):
        return _pass("webmail", "Roundcube files present", "webmail")
    if runner.exists(f"/home/{cfg.domain_user}/public_html/roundcube/index.php"):
        return _pass("webmail", "Roundcube under virtual server", "webmail")
    return _fail("webmail", "Roundcube not found", "webmail")


def _check_vnc(cfg: SetupConfig, runner: Runner) -> Check:
    if runner.dry_run:
        return _pass("VNC", f"port {cfg.vnc.port}", "vnc")
    if runner.exists("/etc/systemd/system/vncserver.service"):
        if runner.live_system and not (
            runner.service_is_active("vncserver") or port_open("127.0.0.1", cfg.vnc.port)
        ):
            return _fail("VNC", f"unit present but nothing listening on {cfg.vnc.port}", "vnc")
        return _pass("VNC", f"port {cfg.vnc.port}", "vnc")
    if runner.live_system and (runner.service_is_active("vncserver") or port_open("127.0.0.1", cfg.vnc.port)):
        return _pass("VNC", f"port {cfg.vnc.port}", "vnc")
    return _fail("VNC", f"nothing listening on {cfg.vnc.port}", "vnc")


def _check_control_panel(cfg: SetupConfig, runner: Runner) -> Check:
    if cfg.uses_1panel:
        if runner.dry_run:
            return _pass("1Panel", f"port {cfg.onepanel.port} (dry-run)", "panel")
        if (
            runner.which("1pctl")
            or runner.exists("/usr/local/bin/1pctl")
            or runner.exists("/usr/bin/1pctl")
            or (runner.live_system and port_open("127.0.0.1", cfg.onepanel.port))
            or (runner.live_system and runner.service_is_active("1panel"))
            or (runner.live_system and runner.service_is_active("1panel-core"))
        ):
            return _pass("1Panel", f"port {cfg.onepanel.port} /{cfg.onepanel.entrance}", "panel")
        return _fail("1Panel", "1pctl not found and panel port is closed", "panel")
    webmin = _check_listening(runner, "Webmin", 10000, "panel") if runner.live_system or runner.dry_run else None
    if runner.dry_run:
        return _pass("Virtualmin", "dry-run", "panel")
    if runner.exists("/usr/sbin/virtualmin") or runner.exists("/usr/bin/virtualmin") or runner.which("virtualmin"):
        if runner.live_system:
            if webmin and not webmin.passed:
                return webmin
            if cfg.webmail == "usermin":
                usermin = _check_listening(runner, "Usermin", 20000, "panel")
                if not usermin.passed:
                    return usermin
            result = runner.run(["virtualmin", "check-config"], check=False)
            if result.returncode == 0:
                return _pass("Virtualmin", "check-config ok", "panel")
            return _fail("Virtualmin", "check-config reported problems", "panel")
        return _pass("Virtualmin", "CLI present", "panel")
    if webmin and not webmin.passed:
        return webmin
    return _fail("Virtualmin", "virtualmin CLI missing", "panel")


def _send_test_mail(cfg: SetupConfig, runner: Runner) -> Check:
    to = cfg.send_test_mail_to
    sender = f"admin@{cfg.domain}"
    if runner.dry_run:
        return _pass("test mail", f"would send to {to}", "mail")
    if not runner.which("swaks"):
        return _fail("test mail", "swaks is not installed", "mail")
    warn(f"Sending test message to {to} via local Postfix (Mailbaby relay)")
    result = runner.run(
        [
            "swaks",
            "--to",
            to,
            "--from",
            sender,
            "--server",
            "127.0.0.1",
            "--port",
            "25",
            "--header",
            f"Subject: cije-vps-installer test for {cfg.domain}",
            "--body",
            f"Test message from {cfg.hostname} at {time.ctime()}\n",
        ],
        check=False,
    )
    out = result.stdout or ""
    if result.returncode == 0 and ("250" in out or runner.dry_run):
        return _pass("test mail", f"accepted for {to}", "mail")
    return _fail("test mail", out[-400:] or f"exit {result.returncode}", "mail")


def as_result_map(checks: list[Check]) -> dict[str, bool]:
    return {check.name: check.passed for check in checks}


# Imported lazily-looking constants for webmail paths (avoid circular imports at top of checks).
SNAPPYMAIL_INDEX = "/var/lib/snappymail/index.php"
CYPHT_INDEX = "/var/lib/cypht/index.php"
