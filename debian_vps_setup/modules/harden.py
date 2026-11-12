"""Turn /root/scripts/harden into menu actions, including adding an SSH key.

The canonical script lives on the target at ``/root/scripts/harden/`` (the copy
from cije.live). This module does not invent that script. It reads whichever
shell or Python files are in that directory, turns their options into actions,
and always offers an SSH public-key install so password logins can be turned
off without locking the operator out.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from debian_vps_setup.console import error, heading, info, ok, warn
from debian_vps_setup.runner import Runner

HARDEN_DIR = "/root/scripts/harden"
ENTRY_NAMES = ("harden", "harden.sh", "harden.py", "main.sh", "run.sh")
KEY_OPTION_NAMES = {
    "ssh-key",
    "ssh_key",
    "add-key",
    "add-ssh-key",
    "pubkey",
    "public-key",
    "authorized-key",
    "authorized-keys",
    "key",
}
FILE_VALUE_NAMES = {"FILE", "PATH", "KEYFILE", "PUBKEY", "KEYPATH"}
PUBKEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|"
    r"ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)"
    r"\s+\S+(?:\s+\S.*)?$"
)
_OPT_LINE = re.compile(
    r"^\s*(?:-\w,\s*)?(--[a-z0-9][a-z0-9-]*)(?:=|\s+|$)(.*)$",
    re.IGNORECASE,
)
_CASE_ARM = re.compile(r"(--[a-z0-9][a-z0-9-]*)([=*|][^\)]*)?\)", re.IGNORECASE)
_FLAG_RE = re.compile(r"^--[a-z0-9][a-z0-9-]*$", re.IGNORECASE)
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class ParsedOption:
    name: str
    flag: str
    takes_value: bool
    value_name: str = ""
    help: str = ""


@dataclass(frozen=True)
class HardenAction:
    id: str
    label: str
    detail: str
    kind: str
    flag: str = ""
    takes_value: bool = False
    value_name: str = ""
    script: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "kind": self.kind,
            "flag": self.flag,
            "takes_value": self.takes_value,
            "value_name": self.value_name,
            "script": self.script,
        }


@dataclass
class HardenPlan:
    found: bool
    directory: str
    script: str
    actions: list[HardenAction] = field(default_factory=list)
    key_flag: str = ""
    key_value_name: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "directory": self.directory,
            "script": self.script,
            "key_flag": self.key_flag,
            "note": self.note,
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass
class HardenResult:
    ok: bool
    action: str
    detail: str
    script: str = ""
    installed: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "action": self.action,
            "detail": self.detail,
            "script": self.script,
            "installed": self.installed,
            "command": self.command,
        }


def parse_help(text: str) -> list[ParsedOption]:
    """Read option lines from a usage message or from the script source."""
    found: list[ParsedOption] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        match = _OPT_LINE.match(line)
        if not match:
            continue
        flag = match.group(1)
        name = flag[2:].lower()
        if name in seen or name in {"help", "version"}:
            continue
        rest = (match.group(2) or "").strip()
        value_name = ""
        takes = False
        if rest.startswith("="):
            rest = rest[1:].strip()
        if rest:
            token = rest.split()[0].strip("[]<>,")
            if token.isupper() or ("_" in token and token.replace("_", "").isupper()):
                takes = True
                value_name = token.upper()
                rest = rest.split(None, 1)[1] if len(rest.split(None, 1)) > 1 else ""
        seen.add(name)
        found.append(ParsedOption(name, flag, takes, value_name, rest.strip()))
    return found


def parse_case_arms(text: str) -> list[ParsedOption]:
    found: list[ParsedOption] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        if ")" not in raw:
            continue
        for match in _CASE_ARM.finditer(raw):
            flag = match.group(1)
            name = flag[2:].lower()
            if name in seen or name in {"help", "version"}:
                continue
            modifier = match.group(2) or ""
            takes = "*" in modifier or "=" in modifier or _is_key_name(name) or name in {"port", "user"}
            value_name = "KEY" if _is_key_name(name) else ("PORT" if name == "port" else "")
            seen.add(name)
            found.append(ParsedOption(name, flag, takes, value_name, ""))
    return found


def _is_key_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in KEY_OPTION_NAMES:
        return True
    return "ssh" in lowered and "key" in lowered


def user_home(user: str) -> str:
    if user == "root":
        return "/root"
    return f"/home/{user}"


def normalize_public_key(text: str) -> str:
    if "PRIVATE KEY" in text or "-----BEGIN" in text:
        raise ValueError("that looks like a private key; paste the public key only")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if len(lines) != 1:
        raise ValueError("paste exactly one public key line")
    if not PUBKEY_RE.match(lines[0]):
        raise ValueError("not an OpenSSH public key")
    if len(lines[0]) > 2000:
        raise ValueError("public key is too long")
    return lines[0]


def normalize_user(user: str) -> str:
    cleaned = (user or "root").strip()
    if not _USER_RE.match(cleaned):
        raise ValueError("user must be a short unix name")
    return cleaned


def _scripts_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix in {".sh", ".py"} or os.access(path, os.X_OK):
            files.append(path)
    return files


def locate_harden(runner: Runner) -> tuple[Path | None, list[Path]]:
    """Return the harden directory and the scripts inside it, under ``--root``."""
    directory = runner.resolve(HARDEN_DIR)
    scripts = _scripts_in(directory)
    return (directory if scripts else None), scripts


def _entry(scripts: list[Path]) -> Path | None:
    by_name = {path.name: path for path in scripts}
    for name in ENTRY_NAMES:
        if name in by_name:
            return by_name[name]
    return scripts[0] if scripts else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def options_for_script(path: Path) -> list[ParsedOption]:
    text = _read(path)
    parsed = parse_help(text)
    if parsed:
        return parsed
    return parse_case_arms(text)


def describe_harden(runner: Runner) -> HardenPlan:
    directory, scripts = locate_harden(runner)
    actions = [
        HardenAction(
            "ssh-key",
            "Add an SSH public key",
            "Install one public key into authorized_keys before password logins are disabled.",
            "ssh-key",
        )
    ]
    if not scripts or directory is None:
        return HardenPlan(
            found=False,
            directory=HARDEN_DIR,
            script="",
            actions=actions,
            note=(
                f"No harden script in {HARDEN_DIR}. "
                "Copy /root/scripts/harden from cije.live onto this machine to unlock its options. "
                "Adding an SSH key still works."
            ),
        )

    entry = _entry(scripts)
    assert entry is not None
    options = options_for_script(entry)
    key_flag = ""
    key_value_name = ""
    for option in options:
        if _is_key_name(option.name):
            key_flag = option.flag
            key_value_name = option.value_name
            continue
        actions.append(
            HardenAction(
                f"flag:{option.name}",
                option.flag + (f" {option.value_name}" if option.takes_value and option.value_name else ""),
                option.help or f"Pass {option.flag} to {entry.name}",
                "flag",
                flag=option.flag,
                takes_value=option.takes_value,
                value_name=option.value_name,
                script=str(entry),
            )
        )
    actions.insert(
        0,
        HardenAction(
            "run",
            f"Run {entry.name}",
            "Apply the harden script with no extra arguments.",
            "run",
            script=str(entry),
        ),
    )
    for extra in scripts:
        if extra == entry:
            continue
        actions.append(
            HardenAction(
                f"run:{extra.name}",
                f"Run {extra.name}",
                f"Run {extra.name} from {HARDEN_DIR} with no extra arguments.",
                "run",
                script=str(extra),
            )
        )
    return HardenPlan(
        found=True,
        directory=str(directory),
        script=str(entry),
        actions=actions,
        key_flag=key_flag,
        key_value_name=key_value_name,
        note=f"Using {entry.name} from {HARDEN_DIR}. Each script option is a separate action.",
    )


def install_public_key(runner: Runner, user: str, public_key: str) -> str:
    user = normalize_user(user)
    key = normalize_public_key(public_key)
    home = user_home(user)
    ssh_dir = f"{home}/.ssh"
    dest = f"{ssh_dir}/authorized_keys"
    pub_copy = f"{ssh_dir}/cije-added.pub"
    if not runner.dry_run:
        folder = runner.resolve(ssh_dir)
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
    runner.write_file(pub_copy, key + "\n", mode=0o644)
    runner.append_unique(dest, key)
    if not runner.dry_run:
        runner.chmod(dest, 0o600)
        if runner.live_system:
            runner.run(["chown", "-R", f"{user}:{user}", runner.resolve(ssh_dir).as_posix()], check=False)
    ok(f"SSH public key installed for {user} at {dest}")
    return dest


def _invoke(script: Path) -> list[str]:
    first = ""
    try:
        first = script.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except OSError:
        first = ""
    if "python" in first or script.suffix == ".py":
        return ["python3", str(script)]
    return ["bash", str(script)]


def _safe_value(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or "\n" in cleaned or "\x00" in cleaned:
        raise ValueError("option value is empty or has a newline")
    if len(cleaned) > 300:
        raise ValueError("option value is too long")
    return cleaned


def _run_script(runner: Runner, script: Path, args: list[str]) -> tuple[int, list[str]]:
    command = _invoke(script) + args
    result = runner.run(command, check=False)
    if result.returncode == 0:
        ok(f"harden script finished: {script.name}")
    else:
        error(f"harden script exited {result.returncode}")
    return result.returncode, command


def apply_harden(
    runner: Runner,
    *,
    action: str,
    public_key: str = "",
    user: str = "root",
    flag: str = "",
    value: str = "",
) -> HardenResult:
    plan = describe_harden(runner)
    chosen = next((item for item in plan.actions if item.id == action or item.flag == action), None)
    if action in {"ssh-key", "add-ssh-key"}:
        chosen = next(item for item in plan.actions if item.kind == "ssh-key")
    if chosen is None and flag:
        chosen = next((item for item in plan.actions if item.flag == flag), None)
    if chosen is None:
        raise ValueError(f"unknown harden action {action!r}")

    if chosen.kind == "ssh-key":
        account = normalize_user(user)
        key = normalize_public_key(public_key)
        installed = install_public_key(runner, account, key)
        command: list[str] = []
        if plan.found and plan.key_flag and plan.script:
            if plan.key_value_name in FILE_VALUE_NAMES:
                payload = str(runner.resolve(f"{user_home(account)}/.ssh/cije-added.pub"))
            else:
                payload = key
            code, command = _run_script(runner, Path(plan.script), [plan.key_flag, payload])
            if code != 0:
                return HardenResult(
                    False,
                    chosen.id,
                    "key installed, but the harden script failed",
                    plan.script,
                    installed,
                    command,
                )
        detail = f"public key installed for {account}"
        if plan.key_flag:
            detail += f" and passed to {plan.key_flag}"
        elif not plan.found:
            detail += f"; harden script not found in {HARDEN_DIR}"
        return HardenResult(True, chosen.id, detail, plan.script, installed, command)

    if not plan.found or not chosen.script:
        return HardenResult(False, chosen.id, plan.note or f"harden script not found in {HARDEN_DIR}")

    script = Path(chosen.script)
    if chosen.kind == "run":
        code, command = _run_script(runner, script, [])
        return HardenResult(code == 0, chosen.id, "ran " + script.name, str(script), "", command)

    if not _FLAG_RE.match(chosen.flag):
        raise ValueError("refusing to pass a flag that is not a long option")
    args = [chosen.flag]
    if chosen.takes_value:
        args.append(_safe_value(value))
    code, command = _run_script(runner, script, args)
    detail = " ".join(args)
    return HardenResult(code == 0, chosen.id, detail, str(script), "", command)


def print_plan(plan: HardenPlan) -> None:
    heading("Secure this system")
    if plan.note:
        info(plan.note)
    if plan.found:
        warn("The harden script can disable password SSH. Add a public key before you run it.")
    for index, action in enumerate(plan.actions, start=1):
        suffix = f" — {action.detail}" if action.detail and action.kind != "flag" else ""
        if action.kind == "flag" and action.detail:
            suffix = f" — {action.detail}"
        print(f"  {index}) {action.label}{suffix}")
    print(f"  {len(plan.actions) + 1}) Back")
