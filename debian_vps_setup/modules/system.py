"""Hostname, hosts file, swap, and baseline packages."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.preflight import detect_ip, mem_available_mb
from debian_vps_setup.runner import Runner

FAIL2BAN_JAIL = """[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
"""

BASE_PACKAGES = [
    "ca-certificates",
    "curl",
    "wget",
    "gnupg",
    "sudo",
    "locales",
    "unzip",
    "tar",
    "openssl",
    "perl",
    "quota",
    "rsync",
    "screen",
    "tmux",
    "vim",
    "net-tools",
    "iproute2",
    "cron",
    "logrotate",
    "fail2ban",
    "libsasl2-modules",
    "sasl2-bin",
    "swaks",
]


def configure(cfg: SetupConfig, runner: Runner) -> None:
    info("Updating apt indexes")
    runner.apt_update()
    info("Installing baseline packages")
    runner.apt_install(BASE_PACKAGES)
    runner.run(["apt-get", "install", "-y", "bind9-dnsutils"], check=False)
    runner.run(["apt-get", "install", "-y", "dnsutils"], check=False)

    _set_hostname(cfg, runner)
    _set_timezone(cfg, runner)
    _ensure_swap(runner)
    _enable_fail2ban(runner)
    _ensure_site_user(cfg, runner)
    ok("base system ready")


def _ensure_site_user(cfg: SetupConfig, runner: Runner) -> None:
    user = cfg.domain_user
    home = cfg.home_dir
    docroot = cfg.document_root
    if cfg.uses_virtualmin:
        info(
            f"Site user {user} will be created by Virtualmin with home {home} "
            f"and website {docroot}"
        )
        return
    if runner.dry_run:
        return
    exists = runner.run(["id", user], check=False)
    if exists.returncode != 0:
        runner.run(["useradd", "-m", "-s", "/bin/bash", "-d", home, user])
    password = cfg.sudo_password or cfg.admin_password
    runner.run(["chpasswd"], input_text=f"{user}:{password}\n")
    runner.run(["mkdir", "-p", docroot, f"{home}/Maildir/cur", f"{home}/Maildir/new", f"{home}/Maildir/tmp"])
    runner.run(["chown", "-R", f"{user}:{user}", home], check=False)
    runner.run(["chmod", "755", home], check=False)
    runner.run(["chmod", "755", docroot], check=False)
    grant_sudo(cfg, runner)
    if cfg.grant_sudo:
        ok(f"{user} can sudo and owns {docroot}")


def grant_sudo(cfg: SetupConfig, runner: Runner) -> None:
    if not cfg.grant_sudo:
        return
    user = cfg.domain_user
    runner.run(["usermod", "-aG", "sudo", user], check=False)
    runner.write_file(
        f"/etc/sudoers.d/{user}",
        f"{user} ALL=(ALL) ALL\n",
        mode=0o440,
    )


def drop_unix_user_keep_home(user: str, runner: Runner) -> None:
    """Remove a unix account so Virtualmin can recreate it; leave the home tree."""
    if not user or user in {"root", "nobody"}:
        return
    runner.run(["pkill", "-9", "-u", user], check=False)
    runner.run(["userdel", user], check=False)


def hosts_body(cfg: SetupConfig, ip: str) -> str:
    return (
        "127.0.0.1\tlocalhost\n"
        f"{ip}\t{cfg.hostname} {cfg.hostname.split('.')[0]}\n"
        f"{ip}\t{cfg.domain} www.{cfg.domain} mail.{cfg.domain} "
        f"webmail.{cfg.domain} ftp.{cfg.domain}\n"
        "\n"
        "::1\tlocalhost ip6-localhost ip6-loopback\n"
        "ff02::1\tip6-allnodes\n"
        "ff02::2\tip6-allrouters\n"
    )


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite installer-owned hostname, hosts, and fail2ban snippets."""
    ip = detect_ip(runner) or "127.0.1.1"
    runner.write_file("/etc/hostname", cfg.hostname + "\n")
    runner.write_file("/etc/hosts", hosts_body(cfg, ip))
    runner.write_file("/etc/fail2ban/jail.d/debian-vps-setup.conf", FAIL2BAN_JAIL)
    if cfg.grant_sudo:
        user = cfg.domain_user
        runner.write_file(f"/etc/sudoers.d/{user}", f"{user} ALL=(ALL) ALL\n", mode=0o440)


def _set_hostname(cfg: SetupConfig, runner: Runner) -> None:
    info(f"Setting hostname to {cfg.hostname}")
    runner.run(["hostnamectl", "set-hostname", cfg.hostname], check=False)
    ip = detect_ip(runner) or "127.0.1.1"
    runner.write_file("/etc/hostname", cfg.hostname + "\n")
    runner.write_file("/etc/hosts", hosts_body(cfg, ip))


def _set_timezone(cfg: SetupConfig, runner: Runner) -> None:
    tz = cfg.timezone or "UTC"
    info(f"Timezone {tz}")
    runner.run(["timedatectl", "set-timezone", tz], check=False)


def _ensure_swap(runner: Runner) -> None:
    ram = mem_available_mb()
    if ram >= 4096:
        info("RAM is sufficient; not adding swap")
        return
    if runner.exists("/swapfile"):
        info("swapfile already present")
        return
    # 2 GiB swap on small VPS instances so Virtualmin + ClamAV can finish.
    size_mb = 2048
    warn(f"{ram} MiB RAM — creating a {size_mb} MiB swap file")
    if runner.dry_run:
        return
    runner.run(["fallocate", "-l", f"{size_mb}M", "/swapfile"], check=False)
    if not runner.exists("/swapfile"):
        runner.run(["dd", "if=/dev/zero", "of=/swapfile", "bs=1M", f"count={size_mb}", "status=none"])
    runner.chmod("/swapfile", 0o600)
    runner.run(["mkswap", "/swapfile"], check=False)
    runner.run(["swapon", "/swapfile"], check=False)
    runner.append_unique("/etc/fstab", "/swapfile none swap sw 0 0")


def _enable_fail2ban(runner: Runner) -> None:
    runner.write_file("/etc/fail2ban/jail.d/debian-vps-setup.conf", FAIL2BAN_JAIL)
    runner.systemctl("enable", "fail2ban", check=False)
    runner.systemctl("restart", "fail2ban", check=False)
