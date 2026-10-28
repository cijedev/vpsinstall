"""Let's Encrypt certificates for the site, mail, FTP, and panels."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import Runner

# Virtualmin install-service-cert does not accept "proftpd".
SERVICES = ("postfix", "dovecot", "webmin", "usermin")


def configure(cfg: SetupConfig, runner: Runner) -> None:
    if not cfg.ssl.enabled:
        warn("SSL step skipped (--no-ssl)")
        return

    runner.apt_install(["certbot"])
    if cfg.web_bundle == "LEMP":
        runner.apt_install(["python3-certbot-nginx"])
    else:
        runner.apt_install(["python3-certbot-apache"])

    hosts = cfg.ssl_hosts()
    info("Requesting Let's Encrypt certificates for: " + ", ".join(hosts))
    if cfg.uses_virtualmin and (runner.which("virtualmin") or runner.exists("/usr/sbin/virtualmin")):
        cmd = [
            "virtualmin",
            "generate-letsencrypt-cert",
            "--domain",
            cfg.domain,
            "--email-always",
            "--web",
        ]
        for host in hosts:
            cmd.extend(["--host", host])
        if cfg.ssl.staging:
            cmd.append("--staging")
        result = runner.run(cmd, check=False)
        if not runner.dry_run and result.returncode != 0:
            warn("Virtualmin certificate request failed; trying certbot directly")
            _certbot(cfg, runner, hosts)
        else:
            _install_service_certs(cfg, runner)
    else:
        _certbot(cfg, runner, hosts)

    from debian_vps_setup.modules.ftp import apply_tls_certificate

    apply_tls_certificate(cfg, runner)

    ok("SSL certificates requested")
    warn("Certificates only succeed when the domain's A records already point at this VPS.")


def _certbot(cfg: SetupConfig, runner: Runner, hosts: list[str]) -> None:
    webroot_flag = ["--apache"] if cfg.web_bundle != "LEMP" else ["--nginx"]
    cmd = [
        "certbot",
        "--non-interactive",
        "--agree-tos",
        "--email",
        cfg.admin_email,
        *webroot_flag,
        "-d",
        cfg.domain,
    ]
    for host in hosts:
        if host != cfg.domain:
            cmd.extend(["-d", host])
    if cfg.ssl.staging:
        cmd.append("--staging")
    runner.run(cmd, check=False)


def _install_service_certs(cfg: SetupConfig, runner: Runner) -> None:
    for service in SERVICES:
        runner.run(
            [
                "virtualmin",
                "install-service-cert",
                "--domain",
                cfg.domain,
                "--add-domain",
                "--service",
                service,
            ],
            check=False,
        )
