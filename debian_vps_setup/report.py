"""Human-readable install report written to /root."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.dns import render as render_dns
from debian_vps_setup.modules.onepanel import access_url as onepanel_url
from debian_vps_setup.runner import Runner


def _panel_lines(cfg: SetupConfig, ip_text: str) -> list[str]:
    if cfg.uses_1panel:
        return [
            "Control panel",
            "  Choice: 1Panel",
            f"  URL: {onepanel_url(cfg, ip_text)}",
            f"  Also: http://{cfg.hostname}:{cfg.onepanel.port}/{cfg.onepanel.entrance}",
            f"  Username: {cfg.onepanel.username}",
            f"  Password: {cfg.onepanel.password}",
            f"  Security entrance: /{cfg.onepanel.entrance}",
            "  Retrieve later: 1pctl user-info",
            "  Open this port in the InterServer firewall as well as locally.",
            "  Do not install OpenResty from the 1Panel app store on ports 80/443",
            "  while Apache/Nginx from this installer is already bound there.",
        ]
    return [
        "Control panel",
        "  Choice: Virtualmin + Webmin",
        f"  Webmin / Virtualmin: https://{cfg.hostname}:10000/  or https://{ip_text}:10000/",
        "  Login: root  (or the sudo user on this VPS)",
        f"  Virtual server user: {cfg.domain_user}",
        f"  Virtual server password: {cfg.admin_password}",
        f"  Usermin: https://{ip_text}:20000/",
    ]


def build_report(
    cfg: SetupConfig,
    *,
    ip: str | None,
    results: dict[str, bool],
) -> str:
    ip_text = ip or "<server-ip>"
    vnc_line = (
        f"VNC: {ip_text}:{cfg.vnc.port}  (display :{cfg.vnc.display}, password in this file)"
        if cfg.vnc.enabled
        else "VNC: disabled"
    )
    if cfg.webmail == "roundcube":
        webmail_urls = [
            f"https://{cfg.webmail_host}/",
            f"https://{cfg.domain}/roundcube/",
        ]
    elif cfg.webmail == "usermin":
        webmail_urls = [
            f"https://{cfg.webmail_host}/",
            f"https://{ip_text}:20000/",
        ]
    else:
        webmail_urls = [
            f"https://{cfg.webmail_host}/",
            f"https://{cfg.domain}/{cfg.webmail}/",
        ]

    failed = [name for name, ok in results.items() if not ok]
    status = "ALL CHECKS PASSED" if not failed else "COMPLETED WITH FAILURES: " + ", ".join(failed)

    mailbox_user = f"admin@{cfg.domain}" if cfg.create_admin_mailbox else ""
    lines = [
        "Cije VPS Installer report",
        f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Status: {status}",
        "",
        "Keep this file mode 600. It contains passwords.",
        "",
        "Identity",
        f"  Domain:     {cfg.domain}",
        f"  Hostname:   {cfg.hostname}",
        f"  Admin:      {cfg.admin_email}",
        f"  Server IP:  {ip_text}",
        f"  Site user:  {cfg.domain_user} (sudo={'yes' if cfg.grant_sudo else 'no'})",
        f"  Home:       {cfg.home_dir}",
        f"  SSH:        ssh {cfg.domain_user}@{ip_text}",
        "",
        *_panel_lines(cfg, ip_text),
        "",
        "Web",
        f"  Site: https://{cfg.domain}/",
        f"  www:  https://{cfg.www_host}/",
        f"  Stack: {cfg.web_bundle}",
        f"  Document root: {cfg.document_root}",
        f"  Site files for {cfg.domain} are served from {cfg.document_root}",
        "",
        "Mail",
        f"  IMAP: {cfg.mail_hostname}  port 993 (SSL)",
        f"  SMTP submission: {cfg.mail_hostname}  port 587 (STARTTLS)",
        f"  Mailbox: {mailbox_user}" if mailbox_user else "  Mailbox: create users in the control panel",
        f"  Mailbox password: {cfg.admin_password}" if cfg.create_admin_mailbox else "",
        (
            f"  Outbound relay: {cfg.mailbaby.relayhost()}  user {cfg.mailbaby.username}"
            if cfg.mailbaby_enabled
            else "  Outbound relay: none (Mailbaby not configured)"
        ),
        "",
        "Webmail",
        f"  Choice: {cfg.webmail}",
        *[f"  URL: {url}" for url in webmail_urls],
        "",
        "FTP",
        f"  ProFTPD: {cfg.ftp_host}  port 21 (FTPS preferred)",
        f"  User: {cfg.domain_user}",
        "",
        "Desktop / InterServer",
        f"  {vnc_line}",
        f"  VNC password: {cfg.vnc.password}" if cfg.vnc.enabled else "",
        "  InterServer's built-in KVM console is hypervisor VNC and works without this.",
        "  This guest VNC server provides an XFCE desktop the panel (or a VNC client) can open.",
        "  Open TCP 5901 in InterServer's network firewall if the panel cannot attach.",
        "",
        "DNS",
        render_dns(cfg, ip),
        "",
        "Checks",
    ]
    for name, passed in results.items():
        mark = "PASS" if passed else "FAIL"
        lines.append(f"  [{mark}] {name}")
    lines.extend(
        [
            "",
            "Re-run tests:  cije-vps-installer test --config /root/vps-setup.json",
            "Re-run one step: cije-vps-installer run --config /root/vps-setup.json --only mail",
            "Troubleshoot:  cije-vps-installer troubleshoot --config /root/vps-setup.json --yes",
            "Snapshots:     cije-vps-installer backup   /   cije-vps-installer restore",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def write_report(runner: Runner, cfg: SetupConfig, text: str) -> Path:
    path = "/root/vps-setup-report.txt"
    runner.write_file(path, text + "\n", mode=0o600)
    runner.write_file("/root/vps-setup.json", _safe_config_json(cfg), mode=0o600)
    return runner.resolve(path)


def _safe_config_json(cfg: SetupConfig) -> str:
    import json

    return json.dumps(cfg.to_dict(), indent=2) + "\n"
