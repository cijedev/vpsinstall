"""Install 1Panel (modern Docker control panel) unattended."""

from __future__ import annotations

from pathlib import Path

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import Runner

INSTALLER_PATH = Path("/root/1panel-quick-start.sh")
INSTALL_URLS = [
    "https://resource.1panel.pro/v2/quick_start.sh",
    "https://resource.fit2cloud.com/1panel/package/v2/quick_start.sh",
]


def onepanel_present(runner: Runner) -> bool:
    return bool(
        runner.which("1pctl")
        or runner.exists("/usr/bin/1pctl")
        or runner.exists("/usr/local/bin/1pctl")
        or runner.exists("/etc/systemd/system/1panel.service")
        or runner.exists("/etc/systemd/system/1panel-core.service")
    )


def install_argv(cfg: SetupConfig) -> list[str]:
    panel = cfg.onepanel
    return [
        "bash",
        str(INSTALLER_PATH),
        "--non-interactive",
        "--lang",
        "en",
        "--install-dir",
        panel.install_dir,
        "--port",
        str(panel.port),
        "--entrance",
        panel.entrance,
        "--username",
        panel.username,
        "--password",
        panel.password,
        "--install-docker",
        "y",
    ]


def install_env(cfg: SetupConfig) -> dict[str, str]:
    panel = cfg.onepanel
    return {
        "PANEL_NON_INTERACTIVE": "true",
        "PANEL_LANG": "en",
        "PANEL_INSTALL_DIR": panel.install_dir,
        "PANEL_PORT": str(panel.port),
        "PANEL_ENTRANCE": panel.entrance,
        "PANEL_USERNAME": panel.username,
        "PANEL_PASSWORD": panel.password,
        "PANEL_INSTALL_DOCKER": "y",
    }


def access_url(cfg: SetupConfig, ip: str | None) -> str:
    host = ip or cfg.hostname or "SERVER_IP"
    return f"http://{host}:{cfg.onepanel.port}/{cfg.onepanel.entrance}"


def install(cfg: SetupConfig, runner: Runner) -> None:
    if onepanel_present(runner) and not runner.dry_run:
        warn("1Panel is already installed; skipping the upstream installer")
        _post_install(cfg, runner)
        return

    info("Downloading the 1Panel installer")
    downloaded = False
    for url in INSTALL_URLS:
        result = runner.run(["curl", "-fsSL", url, "-o", str(INSTALLER_PATH)], check=False)
        if runner.dry_run or result.returncode == 0:
            downloaded = True
            break
        warn(f"download failed: {url}")
    if not downloaded and not runner.dry_run:
        raise RuntimeError("could not download the 1Panel installer")
    runner.chmod(INSTALLER_PATH, 0o755)

    info(
        f"Installing 1Panel on port {cfg.onepanel.port} "
        f"(security entrance /{cfg.onepanel.entrance}). This also installs Docker."
    )
    runner.run(install_argv(cfg), env=install_env(cfg), capture=False)
    _post_install(cfg, runner)
    ok("1Panel installed")


def _post_install(cfg: SetupConfig, runner: Runner) -> None:
    for unit in ("docker", "1panel", "1panel-core", "1panel-agent"):
        runner.systemctl("enable", unit, check=False)
        runner.systemctl("start", unit, check=False)
    _open_firewall(cfg, runner)
    if runner.which("1pctl") or runner.exists("/usr/local/bin/1pctl"):
        runner.run(["1pctl", "start"], check=False)


def _open_firewall(cfg: SetupConfig, runner: Runner) -> None:
    port = str(cfg.onepanel.port)
    if runner.which("firewall-cmd"):
        runner.run(["firewall-cmd", "--permanent", "--add-port", f"{port}/tcp"], check=False)
        runner.run(["firewall-cmd", "--reload"], check=False)
    elif runner.which("ufw"):
        runner.run(["ufw", "allow", f"{port}/tcp", "comment", "1Panel"], check=False)
