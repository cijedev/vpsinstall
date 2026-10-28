"""ProFTPD with TLS when certificates exist."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok
from debian_vps_setup.preflight import detect_ip
from debian_vps_setup.runner import Runner

SNAKEOIL_CERT = "/etc/ssl/certs/ssl-cert-snakeoil.pem"
SNAKEOIL_KEY = "/etc/ssl/private/ssl-cert-snakeoil.key"

PROFTPD_PASSIVE = """# managed by debian-vps-setup
PassivePorts 49152 65534
MasqueradeAddress {ip}
"""


def tls_config(cert: str, key: str) -> str:
    return f"""# managed by debian-vps-setup
<IfModule mod_tls.c>
  TLSEngine on
  TLSLog /var/log/proftpd/tls.log
  TLSProtocol TLSv1.2 TLSv1.3
  TLSRSACertificateFile {cert}
  TLSRSACertificateKeyFile {key}
  TLSOptions NoSessionReuseRequired
  TLSRequired off
</IfModule>
"""


def apply_tls_certificate(cfg: SetupConfig, runner: Runner) -> None:
    live_cert = f"/etc/letsencrypt/live/{cfg.domain}/fullchain.pem"
    live_key = f"/etc/letsencrypt/live/{cfg.domain}/privkey.pem"
    if runner.exists(live_cert) and runner.exists(live_key):
        cert, key = live_cert, live_key
    else:
        cert, key = SNAKEOIL_CERT, SNAKEOIL_KEY
    runner.write_file("/etc/proftpd/conf.d/debian-vps-setup-tls.conf", tls_config(cert, key))
    if runner.live_system:
        runner.systemctl("restart", "proftpd", check=False)


def configure(cfg: SetupConfig, runner: Runner) -> None:
    runner.apt_install(["proftpd-core", "proftpd-mod-crypto", "openssl"])
    apply_tls_certificate(cfg, runner)
    ip = detect_ip(runner) or ""
    if ip:
        runner.write_file(
            "/etc/proftpd/conf.d/debian-vps-setup-passive.conf",
            PROFTPD_PASSIVE.format(ip=ip),
        )
    info("Enabling ProFTPD")
    runner.systemctl("enable", "proftpd", check=False)
    runner.systemctl("restart", "proftpd", check=False)
    ok("ProFTPD installed")


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite installer-owned ProFTPD snippets (no apt)."""
    apply_tls_certificate(cfg, runner)
    ip = detect_ip(runner) or ""
    if ip:
        runner.write_file(
            "/etc/proftpd/conf.d/debian-vps-setup-passive.conf",
            PROFTPD_PASSIVE.format(ip=ip),
        )
