"""TigerVNC + XFCE so InterServer's desktop/VNC feature can attach."""

from __future__ import annotations

import subprocess

from debian_vps_setup.config import SetupConfig, Vnc
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import Runner

VNC_PACKAGES = [
    "tigervnc-standalone-server",
    "tigervnc-common",
    "tigervnc-tools",
    "xfce4",
    "xfce4-terminal",
    "dbus-x11",
    "xterm",
    "firefox-esr",
]


def xstartup_body() -> str:
    return """#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
[ -r "$HOME/.Xresources" ] && xrdb "$HOME/.Xresources"
export XKL_XMODMAP_DISABLE=1
exec startxfce4
"""


def systemd_unit(cfg: SetupConfig) -> str:
    display = cfg.vnc.display
    localhost = "yes" if cfg.vnc.bind_localhost else "no"
    return f"""[Unit]
Description=TigerVNC server for InterServer desktop (display :{display})
After=network.target

[Service]
Type=forking
User=root
Environment=HOME=/root
PIDFile=/root/.vnc/%H:{display}.pid
ExecStartPre=-/usr/bin/vncserver -kill :{display} > /dev/null 2>&1
ExecStart=/usr/bin/vncserver :{display} -geometry {cfg.vnc.geometry} -depth 24 -localhost {localhost}
ExecStop=/usr/bin/vncserver -kill :{display}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def configure(cfg: SetupConfig, runner: Runner) -> None:
    if not cfg.vnc.enabled:
        warn("VNC desktop skipped")
        return

    info("Installing XFCE and TigerVNC (no display manager — VNC only)")
    runner.apt_install(VNC_PACKAGES, recommends=False)

    runner.write_file("/root/.vnc/xstartup", xstartup_body(), mode=0o755)
    _set_vnc_password(cfg.vnc, runner)
    runner.write_file("/etc/systemd/system/vncserver.service", systemd_unit(cfg))
    runner.run(["systemctl", "daemon-reload"], check=False)
    runner.systemctl("enable", "vncserver.service", check=False)
    runner.systemctl("restart", "vncserver.service", check=False)

    _open_firewall(cfg, runner)
    ok(f"VNC listening on port {cfg.vnc.port} (display :{cfg.vnc.display})")
    warn("Classic VNC uses the first 8 characters of the password.")
    warn("InterServer KVM console is separate; this service is the in-guest XFCE desktop.")


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite installer-owned VNC unit and xstartup (no apt)."""
    if not cfg.vnc.enabled:
        return
    runner.write_file("/root/.vnc/xstartup", xstartup_body(), mode=0o755)
    runner.write_file("/etc/systemd/system/vncserver.service", systemd_unit(cfg))


def _set_vnc_password(vnc: Vnc, runner: Runner) -> None:
    passwd_file = "/root/.vnc/passwd"
    if runner.dry_run:
        runner.write_file(passwd_file, "dry-run\n", mode=0o600)
        return
    dest = runner.resolve(passwd_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if runner.which("vncpasswd"):
        result = subprocess.run(
            ["vncpasswd", "-f"],
            input=(vnc.password + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        dest.write_bytes(result.stdout or vnc.password.encode())
        dest.chmod(0o600)
    else:
        runner.write_file(passwd_file, vnc.password + "\n", mode=0o600)


def _open_firewall(cfg: SetupConfig, runner: Runner) -> None:
    port = str(cfg.vnc.port)
    if runner.which("firewall-cmd"):
        runner.run(["firewall-cmd", "--permanent", "--add-port", f"{port}/tcp"], check=False)
        runner.run(["firewall-cmd", "--reload"], check=False)
    elif runner.which("ufw"):
        runner.run(["ufw", "allow", f"{port}/tcp"], check=False)
