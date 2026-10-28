"""Command execution, file writes, and apt helpers."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from debian_vps_setup.console import info, ok, warn

log = logging.getLogger("debian_vps_setup")

DEFAULT_ENV = {
    "DEBIAN_FRONTEND": "noninteractive",
    "NEEDRESTART_MODE": "a",
    "APT_LISTCHANGES_FRONTEND": "none",
    "UCF_FORCE_CONFFOLD": "1",
}


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, output: str):
        self.cmd = cmd
        self.returncode = returncode
        self.output = output
        super().__init__(f"command failed ({returncode}): {shlex.join(cmd)}\n{output}")


class Runner:
    def __init__(
        self,
        *,
        dry_run: bool = False,
        root: str | Path = "/",
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.root = Path(root)
        self.env = {**os.environ, **DEFAULT_ENV, **(env or {})}

    @property
    def live_system(self) -> bool:
        """True only when commands would change the real host (not --root / --dry-run)."""
        return self.root == Path("/") and not self.dry_run

    def resolve(self, path: str | Path) -> Path:
        path = Path(path)
        if self.root == Path("/"):
            return path
        if path.is_absolute():
            return self.root / path.relative_to("/")
        return self.root / path

    def exists(self, path: str | Path) -> bool:
        return self.resolve(path).exists()

    def which(self, name: str) -> str | None:
        if self.dry_run:
            return name
        found = shutil.which(name)
        return found

    def run(
        self,
        cmd: Sequence[str] | str,
        *,
        check: bool = True,
        capture: bool = True,
        input_text: str | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, str):
            pretty = cmd
            args: Sequence[str] | str = cmd
            shell = True
        else:
            pretty = shlex.join(cmd)
            args = cmd
            shell = False

        log.info("$ %s", pretty)
        if self.dry_run:
            info(f"[dry-run] {pretty}")
            return subprocess.CompletedProcess(args=cmd if not isinstance(cmd, str) else [cmd], returncode=0, stdout="", stderr="")

        merged = dict(self.env)
        if env:
            merged.update(env)
        result = subprocess.run(
            args,
            shell=shell,
            check=False,
            text=True,
            input=input_text,
            cwd=str(cwd) if cwd else None,
            env=merged,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
        output = result.stdout or ""
        if output.strip():
            log.debug(output)
        if check and result.returncode != 0:
            raise CommandError(cmd if not isinstance(cmd, str) else [cmd], result.returncode, output)
        if capture:
            result.stdout = output
        return result

    def run_ok(self, cmd: Sequence[str] | str) -> bool:
        try:
            result = self.run(cmd, check=False)
        except Exception:
            return False
        return result.returncode == 0

    def output(self, cmd: Sequence[str] | str) -> str:
        result = self.run(cmd, check=False)
        return (result.stdout or "").strip()

    def write_file(
        self,
        path: str | Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: str | None = None,
    ) -> Path:
        dest = self.resolve(path)
        if self.dry_run:
            info(f"[dry-run] write {dest} ({oct(mode)})")
            log.debug("contents:\n%s", content)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        os.chmod(dest, mode)
        if owner and self.root == Path("/"):
            self.run(["chown", owner, str(dest)], check=False)
        ok(f"wrote {dest}")
        return dest

    def copy_resolved(self, src: Path, dest: Path) -> None:
        """Copy a resolved path to another resolved path (files only)."""
        if self.dry_run:
            info(f"[dry-run] copy {src} -> {dest}")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def chmod(self, path: str | Path, mode: int) -> None:
        dest = self.resolve(path)
        if self.dry_run:
            info(f"[dry-run] chmod {oct(mode)} {dest}")
            return
        os.chmod(dest, mode)

    def append_unique(self, path: str | Path, line: str) -> None:
        dest = self.resolve(path)
        line = line.rstrip("\n")
        if self.dry_run:
            info(f"[dry-run] append {line!r} -> {dest}")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = dest.read_text(encoding="utf-8") if dest.exists() else ""
        if line in existing.splitlines():
            return
        with dest.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(line + "\n")

    def apt_update(self) -> None:
        self.run(["apt-get", "update", "-y"])

    def apt_install(self, packages: Sequence[str], *, recommends: bool = True) -> None:
        if not packages:
            return
        cmd = ["apt-get", "install", "-y"]
        if not recommends:
            cmd.append("--no-install-recommends")
        cmd.extend(list(packages))
        try:
            self.run(cmd)
        except CommandError:
            warn("batch apt install failed; installing packages individually")
            for pkg in packages:
                single = ["apt-get", "install", "-y"]
                if not recommends:
                    single.append("--no-install-recommends")
                single.append(pkg)
                self.run(single, check=False)

    def systemctl(self, action: str, unit: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(["systemctl", action, unit], check=check)

    def service_is_active(self, unit: str) -> bool:
        if self.dry_run:
            return True
        result = self.run(["systemctl", "is-active", unit], check=False)
        return (result.stdout or "").strip() == "active"

    def listening(self, port: int) -> bool:
        if self.dry_run:
            return True
        # ss is present on current Debian; fall back to /proc.
        if self.which("ss"):
            out = self.output(["ss", "-lntu"])
            return f":{port} " in out or f":{port}\n" in out
        return True

    def reboot_needed(self) -> bool:
        return self.exists("/var/run/reboot-required")
