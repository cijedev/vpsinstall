"""Safety checks before touching the system."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import error, info, ok, warn
from debian_vps_setup.runner import Runner

MIN_RAM_MB = 1024
MIN_DISK_MB = 8 * 1024
SUPPORTED_DEBIAN = {"11", "12", "13"}


def _os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb // 1024
    except OSError:
        pass
    return 0


def disk_free_mb(path: str = "/") -> int:
    usage = os.statvfs(path)
    return int(usage.f_bavail * usage.f_frsize / (1024 * 1024))


def detect_ip(runner: Runner) -> str | None:
    if runner.which("hostname"):
        ip = runner.output(["hostname", "-I"])
        if ip:
            return ip.split()[0]
    return None


class PreflightError(RuntimeError):
    pass


def run_preflight(
    cfg: SetupConfig,
    runner: Runner,
    *,
    require_root: bool = True,
    enforce_debian: bool = True,
) -> dict[str, str]:
    release = _os_release()
    distro = release.get("ID", "")
    version = release.get("VERSION_ID", "")
    pretty = release.get("PRETTY_NAME", platform.platform())

    info(f"OS: {pretty}")
    info(f"Kernel: {platform.release()}")

    problems: list[str] = []
    warnings: list[str] = []

    if require_root and hasattr(os, "geteuid") and os.geteuid() != 0 and not runner.dry_run:
        problems.append("this installer must run as root (sudo -i)")

    if distro != "debian":
        message = (
            f"this installer targets Debian, not {pretty}. Use a fresh Debian 12 or 13 VPS."
        )
        if enforce_debian:
            problems.append(message)
        else:
            warnings.append(message + " (not enforced in dry-run)")
    elif version.split(".")[0] not in SUPPORTED_DEBIAN:
        warnings.append(f"Debian {version} is untested; Virtualmin currently documents 12 and 13.")

    ram = mem_available_mb()
    disk = disk_free_mb("/")
    info(f"Memory: {ram} MiB   Free disk: {disk} MiB")
    if ram and ram < MIN_RAM_MB:
        warnings.append(
            f"only {ram} MiB RAM detected; Virtualmin + mail works better with 2 GiB+ (4 GiB recommended)."
        )
    if disk and disk < MIN_DISK_MB:
        problems.append(f"only {disk} MiB free; need about 8 GiB free for the control panel and mail stack")

    if Path("/usr/sbin/virtualmin").exists() or Path("/usr/bin/virtualmin").exists():
        warnings.append("Virtualmin already appears installed; the Virtualmin step will be skipped.")

    if cfg.hostname == cfg.domain:
        problems.append("hostname and domain must not be identical")

    for item in warnings:
        warn(item)
    if problems:
        for item in problems:
            error(item)
        raise PreflightError("; ".join(problems))

    ok("preflight checks passed")
    ip = detect_ip(runner)
    return {
        "os": pretty,
        "version": version,
        "ram_mb": str(ram),
        "disk_mb": str(disk),
        "ip": ip or "",
    }
