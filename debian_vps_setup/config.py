"""Load, validate, and prompt for setup configuration."""

from __future__ import annotations

import getpass
import json
import re
import secrets
import string
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from debian_vps_setup.console import error, heading, info, warn

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WEBMAIL_CHOICES = ("roundcube", "usermin", "snappymail", "cypht")
STANDALONE_WEBMAIL = ("roundcube", "snappymail", "cypht")
WEBMAIL_LABELS = {
    "roundcube": "Roundcube (Debian packages, IMAP webmail)",
    "snappymail": "SnappyMail (lightweight PHP webmail under the site)",
    "cypht": "Cypht (modular PHP webmail under the site)",
    "usermin": "Usermin (Virtualmin/Webmin webmail on :20000)",
}
WEB_BUNDLES = ("LAMP", "LEMP")
CONTROL_PANELS = ("virtualmin", "1panel")
CONTROL_PANEL_ALIASES = {
    "virtualmin": "virtualmin",
    "webmin": "virtualmin",
    "virtualmin/webmin": "virtualmin",
    "webmin/virtualmin": "virtualmin",
    "1panel": "1panel",
    "onepanel": "1panel",
    "one-panel": "1panel",
    "one_panel": "1panel",
}


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    # Avoid ambiguous characters in printed reports.
    alphabet = alphabet.replace("l", "").replace("I", "").replace("O", "").replace("0", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_des_key(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_token(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_control_panel(value: str) -> str:
    key = (value or "").strip().lower().replace(" ", "")
    if key not in CONTROL_PANEL_ALIASES:
        raise ValueError(
            f"control_panel must be 'virtualmin' (Webmin) or '1panel', not {value!r}"
        )
    return CONTROL_PANEL_ALIASES[key]


@dataclass
class Mailbaby:
    host: str = "relay.mailbaby.net"
    port: int = 25
    username: str = ""
    password: str = ""

    def relayhost(self) -> str:
        host = self.host.strip()
        if self.port in (465, 587):
            inner = host[1:-1] if host.startswith("[") and host.endswith("]") else host
            return f"[{inner}]:{self.port}"
        if ":" in host:
            return host
        return f"{host}:{self.port}"

    def sasl_lookup_keys(self) -> list[str]:
        """Postfix looks up several forms of the nexthop; write all of them."""
        host = self.host.strip()
        if host.startswith("[") and host.endswith("]"):
            bare = host[1:-1]
        else:
            bare = host.split(":")[0]
        keys = [
            bare,
            f"{bare}:{self.port}",
            f"[{bare}]",
            f"[{bare}]:{self.port}",
            self.relayhost(),
        ]
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        out: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out


@dataclass
class Vnc:
    enabled: bool = True
    password: str = ""
    display: int = 1
    geometry: str = "1280x720"
    bind_localhost: bool = False

    @property
    def port(self) -> int:
        return 5900 + int(self.display)


@dataclass
class Ssl:
    enabled: bool = True
    staging: bool = False


@dataclass
class OnePanel:
    port: int = 8888
    username: str = "vpsadmin"
    password: str = ""
    entrance: str = ""
    install_dir: str = "/opt"


@dataclass
class SetupConfig:
    domain: str = ""
    hostname: str = ""
    admin_email: str = ""
    admin_password: str = ""
    timezone: str = "UTC"
    control_panel: str = "virtualmin"
    web_bundle: str = "LAMP"
    webmail: str = "roundcube"
    php_extensions: list[str] = field(
        default_factory=lambda: [
            "mysql",
            "xml",
            "mbstring",
            "curl",
            "zip",
            "gd",
            "intl",
            "imap",
            "bcmath",
            "soap",
            "opcache",
            "cli",
            "fpm",
        ]
    )
    mailbaby: Mailbaby = field(default_factory=Mailbaby)
    vnc: Vnc = field(default_factory=Vnc)
    ssl: Ssl = field(default_factory=Ssl)
    onepanel: OnePanel = field(default_factory=OnePanel)
    create_admin_mailbox: bool = True
    send_test_mail_to: str = ""
    roundcube_db_password: str = ""
    roundcube_des_key: str = ""
    site_user: str = ""
    grant_sudo: bool = True
    sudo_password: str = ""

    @property
    def domain_user(self) -> str:
        if self.site_user:
            label = re.sub(r"[^a-z0-9_-]", "", self.site_user.lower())
            return (label or "site")[:32]
        label = self.domain.split(".")[0] if self.domain else "site"
        label = re.sub(r"[^a-z0-9]", "", label.lower()) or "site"
        return label[:16]

    @property
    def home_dir(self) -> str:
        return f"/home/{self.domain_user}"

    @property
    def mailbaby_enabled(self) -> bool:
        return bool(self.mailbaby.username and self.mailbaby.password)

    @property
    def mail_hostname(self) -> str:
        return f"mail.{self.domain}"

    @property
    def webmail_host(self) -> str:
        return f"webmail.{self.domain}"

    @property
    def www_host(self) -> str:
        return f"www.{self.domain}"

    @property
    def ftp_host(self) -> str:
        return f"ftp.{self.domain}"

    @property
    def uses_virtualmin(self) -> bool:
        return self.control_panel == "virtualmin"

    @property
    def uses_1panel(self) -> bool:
        return self.control_panel == "1panel"

    @property
    def document_root(self) -> str:
        return f"{self.home_dir}/public_html"

    def ssl_hosts(self) -> list[str]:
        hosts = [
            self.domain,
            self.www_host,
            self.mail_hostname,
            self.webmail_host,
            self.ftp_host,
            self.hostname,
        ]
        seen: set[str] = set()
        out: list[str] = []
        for host in hosts:
            if host and host not in seen:
                seen.add(host)
                out.append(host)
        return out

    def fill_defaults(self) -> None:
        self.domain = self.domain.strip().lower().rstrip(".")
        if self.domain.startswith("www."):
            self.domain = self.domain[4:]
        if not self.hostname:
            self.hostname = f"server.{self.domain}" if self.domain else ""
        self.hostname = self.hostname.strip().lower().rstrip(".")
        if not self.admin_email and self.domain:
            self.admin_email = f"admin@{self.domain}"
        self.admin_email = self.admin_email.strip().lower()
        try:
            self.control_panel = normalize_control_panel(self.control_panel or "virtualmin")
        except ValueError:
            pass
        self.web_bundle = (self.web_bundle or "LAMP").upper()
        self.webmail = (self.webmail or "roundcube").strip().lower()
        if self.uses_1panel and self.webmail == "usermin":
            self.webmail = "roundcube"
        if not self.admin_password:
            self.admin_password = generate_password()
        if self.vnc.enabled and not self.vnc.password:
            self.vnc.password = generate_password(8)
        if not self.roundcube_db_password:
            self.roundcube_db_password = generate_password(24)
        if not self.roundcube_des_key:
            self.roundcube_des_key = generate_des_key(24)
        if not self.mailbaby.host:
            self.mailbaby.host = "relay.mailbaby.net"
        if not self.mailbaby.port:
            self.mailbaby.port = 25
        if not self.onepanel.username:
            self.onepanel.username = "vpsadmin"
        if not self.onepanel.password:
            self.onepanel.password = self.admin_password
        if not self.onepanel.entrance:
            self.onepanel.entrance = generate_token(12)
        if not self.onepanel.port:
            self.onepanel.port = 8888
        if not self.onepanel.install_dir:
            self.onepanel.install_dir = "/opt"
        if not self.site_user and self.domain:
            self.site_user = self.domain.split(".")[0]
        if not self.sudo_password:
            self.sudo_password = self.admin_password

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.domain:
            problems.append("domain is required")
        elif not DOMAIN_RE.match(self.domain):
            problems.append(f"domain {self.domain!r} is not a valid DNS name")
        if self.hostname and not DOMAIN_RE.match(self.hostname):
            problems.append(f"hostname {self.hostname!r} is not a valid FQDN")
        if self.hostname and self.domain and self.hostname == self.domain:
            problems.append(
                "hostname must be a server FQDN such as server.example.com, "
                "not the same as the hosted domain"
            )
        if not self.admin_email:
            problems.append("admin_email is required")
        elif not EMAIL_RE.match(self.admin_email):
            problems.append(f"admin_email {self.admin_email!r} is not a valid address")
        if self.webmail not in WEBMAIL_CHOICES:
            problems.append(f"webmail must be one of: {', '.join(WEBMAIL_CHOICES)}")
        try:
            self.control_panel = normalize_control_panel(self.control_panel)
        except ValueError as exc:
            problems.append(str(exc))
        if self.web_bundle not in WEB_BUNDLES:
            problems.append("web_bundle must be LAMP (Apache) or LEMP (Nginx)")
        if self.uses_1panel:
            if self.onepanel.port < 1024 or self.onepanel.port > 65535:
                problems.append("onepanel.port must be between 1024 and 65535")
            if not self.onepanel.username or len(self.onepanel.username) < 3:
                problems.append("onepanel.username must be at least 3 characters")
            if not self.onepanel.password or len(self.onepanel.password) < 8:
                problems.append("onepanel.password must be at least 8 characters")
            if not self.onepanel.entrance or len(self.onepanel.entrance) < 3:
                problems.append("onepanel.entrance (security URL path) must be at least 3 characters")
        if self.mailbaby.username and not self.mailbaby.password:
            problems.append("mailbaby.password is required when a Mailbaby username is set")
        if self.mailbaby.password and not self.mailbaby.username:
            problems.append("mailbaby.username is required when a Mailbaby password is set")
        if self.mailbaby.port not in (25, 465, 587, 2525):
            problems.append("mailbaby.port should be 25, 465, 587, or 2525")
        if self.vnc.enabled:
            if not self.vnc.password:
                problems.append("vnc.password is required when VNC is enabled")
            elif len(self.vnc.password) < 6:
                problems.append("vnc.password must be at least 6 characters")
            if self.vnc.display < 1 or self.vnc.display > 20:
                problems.append("vnc.display must be between 1 and 20")
        if self.send_test_mail_to and not EMAIL_RE.match(self.send_test_mail_to):
            problems.append("send_test_mail_to is not a valid address")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data["admin_password"] = "***"
        data["mailbaby"]["password"] = "***"
        data["vnc"]["password"] = "***"
        data["roundcube_db_password"] = "***"
        data["roundcube_des_key"] = "***"
        data["onepanel"]["password"] = "***"
        data["sudo_password"] = "***"
        return data


def _from_mapping(cls, data: dict[str, Any], nested: dict[str, Any] | None = None):
    nested = nested or {}
    allowed = {item.name for item in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in allowed:
            continue
        if key in nested and isinstance(value, dict):
            kwargs[key] = nested[key](**{k: v for k, v in value.items() if k in {f.name for f in fields(nested[key])}})
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path) -> SetupConfig:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("config file must contain a JSON object")
    cfg = _from_mapping(
        SetupConfig,
        data,
        nested={"mailbaby": Mailbaby, "vnc": Vnc, "ssl": Ssl, "onepanel": OnePanel},
    )
    cfg.fill_defaults()
    return cfg


def config_from_args(args: Any) -> SetupConfig:
    """Build config from CLI flags, optionally overlaying a JSON file."""
    if getattr(args, "config", None):
        cfg = load_config(args.config)
    else:
        cfg = SetupConfig()

    if getattr(args, "domain", None):
        cfg.domain = args.domain
    if getattr(args, "hostname", None):
        cfg.hostname = args.hostname
    if getattr(args, "admin_email", None):
        cfg.admin_email = args.admin_email
    if getattr(args, "admin_password", None):
        cfg.admin_password = args.admin_password
    if getattr(args, "timezone", None):
        cfg.timezone = args.timezone
    if getattr(args, "control_panel", None):
        cfg.control_panel = args.control_panel
    if getattr(args, "web_bundle", None):
        cfg.web_bundle = args.web_bundle
    if getattr(args, "webmail", None):
        cfg.webmail = args.webmail
    if getattr(args, "mailbaby_host", None):
        cfg.mailbaby.host = args.mailbaby_host
    if getattr(args, "mailbaby_port", None):
        cfg.mailbaby.port = args.mailbaby_port
    if getattr(args, "mailbaby_username", None):
        cfg.mailbaby.username = args.mailbaby_username
    if getattr(args, "mailbaby_password", None):
        cfg.mailbaby.password = args.mailbaby_password
    if getattr(args, "vnc_password", None):
        cfg.vnc.password = args.vnc_password
    if getattr(args, "no_vnc", False):
        cfg.vnc.enabled = False
    if getattr(args, "no_ssl", False):
        cfg.ssl.enabled = False
    if getattr(args, "ssl_staging", False):
        cfg.ssl.staging = True
    if getattr(args, "send_test_mail_to", None):
        cfg.send_test_mail_to = args.send_test_mail_to
    if getattr(args, "onepanel_port", None):
        cfg.onepanel.port = args.onepanel_port
    if getattr(args, "onepanel_username", None):
        cfg.onepanel.username = args.onepanel_username
    if getattr(args, "onepanel_password", None):
        cfg.onepanel.password = args.onepanel_password
    if getattr(args, "onepanel_entrance", None):
        cfg.onepanel.entrance = args.onepanel_entrance
    if getattr(args, "site_user", None):
        cfg.site_user = args.site_user
    if getattr(args, "sudo_password", None):
        cfg.sudo_password = args.sudo_password
    if getattr(args, "no_sudo", False):
        cfg.grant_sudo = False

    cfg.fill_defaults()
    return cfg


def _prompt_webmail(*, allow_usermin: bool) -> str:
    info("Webmail options:")
    for name in WEBMAIL_CHOICES:
        if name == "usermin" and not allow_usermin:
            continue
        info(f"  {name:11} — {WEBMAIL_LABELS[name]}")
    allowed = tuple(name for name in WEBMAIL_CHOICES if allow_usermin or name != "usermin")
    choice = ""
    while choice not in allowed:
        choice = _prompt("Webmail", "roundcube").lower()
        if choice not in allowed:
            error("Choose one of: " + ", ".join(allowed))
            choice = ""
    return choice


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    if secret:
        value = getpass.getpass(prompt)
    else:
        value = input(prompt)
    value = value.strip()
    return value or default


def run_wizard() -> SetupConfig:
    heading("Server identity")
    info("Use the domain you will host, for example example.com.")
    info("The machine hostname should be a subdomain, for example server.example.com.")
    domain = ""
    while not domain:
        domain = _prompt("Domain name").lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if not DOMAIN_RE.match(domain):
            error("Enter a full domain name such as example.com")
            domain = ""

    hostname = _prompt("Hostname", f"server.{domain}")
    admin_email = _prompt("Admin email", f"admin@{domain}")
    admin_password = _prompt("Admin password (blank to generate)", secret=True)
    timezone = _prompt("Timezone", "UTC")
    default_user = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower()) or "site"
    site_user = _prompt("Unix user that owns the website", default_user)
    grant_sudo = _prompt("Grant sudo to that user", "Y").lower() not in {"n", "no"}

    heading("Control panel")
    info("Choose one. Do not install both on the same VPS.")
    info("  virtualmin  — Virtualmin + Webmin (classic hosting panel, Usermin included)")
    info("  1panel      — 1Panel (modern Docker panel; standalone webmail: Roundcube, SnappyMail, or Cypht)")
    panel_choice = ""
    while not panel_choice:
        raw = _prompt("Control panel", "virtualmin")
        try:
            panel_choice = normalize_control_panel(raw)
        except ValueError as exc:
            error(str(exc))
            panel_choice = ""

    bundle = _prompt("Web stack LAMP (Apache) or LEMP (Nginx)", "LAMP").upper()
    if panel_choice == "1panel":
        info("Usermin is part of Virtualmin/Webmin; 1Panel uses standalone webmail.")
        webmail = _prompt_webmail(allow_usermin=False)
        onepanel_port = int(_prompt("1Panel port", "8888") or "8888")
        onepanel_user = _prompt("1Panel username", "vpsadmin")
        onepanel_pass = _prompt("1Panel password (blank to reuse admin password)", secret=True)
        onepanel_entrance = _prompt("1Panel security entrance (blank to generate)", "")
        onepanel = OnePanel(
            port=onepanel_port,
            username=onepanel_user,
            password=onepanel_pass,
            entrance=onepanel_entrance,
        )
    else:
        webmail = _prompt_webmail(allow_usermin=True)
        onepanel = OnePanel()

    heading("Mailbaby outbound relay")
    info("Leave the username blank to skip Mailbaby and send mail from this VPS.")
    info("Values come from the Mailbaby client portal when you use their relay.")
    mb_host = _prompt("Mailbaby host", "relay.mailbaby.net")
    mb_port_raw = _prompt("Mailbaby port", "25") or "25"
    try:
        mb_port = int(mb_port_raw)
    except ValueError:
        mb_port = 25
    mb_user = _prompt("Mailbaby username (blank to skip)", "")
    mb_pass = _prompt("Mailbaby password", secret=True) if mb_user else ""

    heading("Desktop (InterServer VNC)")
    info("Installs XFCE + TigerVNC so InterServer's desktop/VNC tab can attach to a GUI.")
    vnc_on = _prompt("Enable VNC desktop", "Y").lower() not in {"n", "no"}
    vnc_password = ""
    if vnc_on:
        vnc_password = _prompt("VNC password (first 8 chars used by classic VNC)", secret=True)

    heading("TLS")
    ssl_on = _prompt("Request Let's Encrypt certificates", "Y").lower() not in {"n", "no"}
    test_mail = _prompt("Send a test message after setup (recipient, or blank to skip)", "")

    cfg = SetupConfig(
        domain=domain,
        hostname=hostname,
        admin_email=admin_email,
        admin_password=admin_password,
        timezone=timezone,
        control_panel=panel_choice,
        web_bundle=bundle,
        webmail=webmail,
        mailbaby=Mailbaby(host=mb_host, port=mb_port, username=mb_user, password=mb_pass),
        vnc=Vnc(enabled=vnc_on, password=vnc_password),
        ssl=Ssl(enabled=ssl_on),
        onepanel=onepanel,
        send_test_mail_to=test_mail,
        site_user=site_user,
        grant_sudo=grant_sudo,
    )
    cfg.fill_defaults()
    problems = cfg.validate()
    if problems:
        for item in problems:
            error(item)
        raise SystemExit(2)
    warn("Admin, VNC, and generated database passwords will be written to /root/vps-setup-report.txt")
    return cfg
