"""Install Webmin and Virtualmin via the official installer."""

from __future__ import annotations

from pathlib import Path

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import CommandError, Runner

INSTALLER_URL = "https://software.virtualmin.com/gpl/scripts/virtualmin-install.sh"
INSTALLER_PATH = Path("/root/virtualmin-install.sh")


def virtualmin_present(runner: Runner) -> bool:
    return bool(
        runner.exists("/usr/sbin/virtualmin")
        or runner.exists("/usr/bin/virtualmin")
        or runner.which("virtualmin")
    )


def install_argv(cfg: SetupConfig) -> list[str]:
    bundle = cfg.web_bundle if cfg.web_bundle in {"LAMP", "LEMP"} else "LAMP"
    cmd = [
        "sh",
        str(INSTALLER_PATH),
        "--bundle",
        bundle,
        "--hostname",
        cfg.hostname,
        "--yes",
        "--force",
    ]
    if not cfg.ssl.enabled:
        cmd.extend(["--no-hostname-ssl"])
    return cmd


def install(cfg: SetupConfig, runner: Runner) -> None:
    if not cfg.uses_virtualmin:
        return
    if virtualmin_present(runner) and not runner.dry_run:
        warn("Virtualmin is already installed; skipping the upstream installer")
        _post_install(cfg, runner)
        return

    info("Downloading the Virtualmin GPL installer")
    runner.run(["curl", "-fsSL", INSTALLER_URL, "-o", str(INSTALLER_PATH)])
    runner.chmod(INSTALLER_PATH, 0o755)

    cmd = install_argv(cfg)
    info(
        "Running Virtualmin install (Apache/Nginx, PHP, Postfix, Dovecot, "
        "ProFTPD, Webmin, Usermin). This takes a while."
    )
    try:
        runner.run(cmd, capture=False)
    except CommandError:
        warn("Virtualmin installer returned an error; retrying")
        runner.run(cmd, capture=False)

    _post_install(cfg, runner)
    ok("Webmin and Virtualmin installed")


def _post_install(cfg: SetupConfig, runner: Runner) -> None:
    runner.systemctl("enable", "webmin", check=False)
    runner.systemctl("start", "webmin", check=False)
    runner.systemctl("enable", "usermin", check=False)
    runner.systemctl("start", "usermin", check=False)
    if runner.which("virtualmin") or runner.exists("/usr/sbin/virtualmin"):
        runner.run(["virtualmin", "check-config"], check=False)
