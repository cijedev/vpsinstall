"""Interactive numbered TTY menu (stdlib only)."""

from __future__ import annotations

import argparse
import sys

from debian_vps_setup.config import SetupConfig, run_wizard
from debian_vps_setup.console import error, heading, info, warn
from debian_vps_setup.modules.verify import COMPONENTS
from debian_vps_setup.pipeline import names as step_names
from debian_vps_setup.runner import Runner
from debian_vps_setup.state import InstallState

MENU_ITEMS = (
    ("1", "Install / resume"),
    ("2", "Run one feature"),
    ("3", "Check configuration"),
    ("4", "Secure this system"),
    ("5", "Debug security lockouts"),
    ("6", "Troubleshoot components"),
    ("7", "Re-test stack"),
    ("8", "Show DNS helper"),
    ("9", "Status (completed steps)"),
    ("10", "System backup / restore"),
    ("11", "Quit"),
)


def cmd_menu(args: argparse.Namespace, runner: Runner, state: InstallState) -> int:
    if not sys.stdin.isatty():
        error(
            "menu requires a TTY; use wizard, run, check, harden, debug, test, "
            "dns, status, troubleshoot, backup, or restore"
        )
        return 2
    heading("Main menu")
    info("Choose an action. You do not need command-line arguments.")
    while True:
        print()
        for key, label in MENU_ITEMS:
            print(f"  {key}) {label}")
        choice = _ask("Select", "1-11 / q").strip().lower()
        if choice in {"11", "q", "quit"}:
            return 0
        if choice in {"1", "i", "install"}:
            code = _menu_install(args, runner, state)
        elif choice in {"2", "feature", "features"}:
            code = _menu_feature(args, runner, state)
        elif choice in {"3", "check"}:
            code = _menu_check(args, runner)
        elif choice in {"4", "harden", "secure"}:
            code = _menu_harden(args, runner)
        elif choice in {"5", "debug"}:
            code = _menu_debug(args, runner)
        elif choice in {"6", "t", "troubleshoot"}:
            code = _menu_troubleshoot(args, runner)
        elif choice in {"7", "test"}:
            code = _menu_test(args, runner)
        elif choice in {"8", "dns"}:
            code = _menu_dns(args, runner)
        elif choice in {"9", "status"}:
            from debian_vps_setup.cli import cmd_status

            code = cmd_status(state)
        elif choice in {"10", "backup", "restore"}:
            code = _menu_backup(args, runner)
        else:
            warn("Enter a number from 1 to 11")
            continue
        if code not in (0, 1):
            warn(f"command exited with status {code}")
    return 0


def _menu_install(args: argparse.Namespace, runner: Runner, state: InstallState) -> int:
    from debian_vps_setup.cli import cmd_run, saved_config_path, try_load_saved_config

    saved = saved_config_path(args.root)
    cfg: SetupConfig | None = None
    if saved.exists() or args.config:
        print()
        print("  1) Interactive wizard (new install)")
        print("  2) Resume / run from saved config")
        print("  3) Back")
        pick = _ask("Select", "1-3").strip()
        if pick in {"3", "b", "back"}:
            return 0
        if pick == "2":
            cfg = try_load_saved_config(args, required=True)
        else:
            cfg = run_wizard()
    else:
        cfg = run_wizard()
    return cmd_run(cfg, args, runner, state)


def _menu_feature(args: argparse.Namespace, runner: Runner, state: InstallState) -> int:
    from debian_vps_setup.cli import cmd_run, try_load_saved_config

    cfg = try_load_saved_config(args, required=True)
    steps = step_names()
    print()
    for index, name in enumerate(steps, start=1):
        print(f"  {index}) {name}")
    print(f"  {len(steps) + 1}) Back")
    pick = _ask("Feature", "number").strip().lower()
    if pick in {str(len(steps) + 1), "b", "back"}:
        return 0
    if not pick.isdigit() or not 1 <= int(pick) <= len(steps):
        error("unknown feature")
        return 2
    previous_only = args.only
    previous_skip = args.skip
    args.only = steps[int(pick) - 1]
    args.skip = None
    try:
        return cmd_run(cfg, args, runner, state)
    finally:
        args.only = previous_only
        args.skip = previous_skip


def _menu_check(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_check, try_load_saved_config

    cfg = try_load_saved_config(args, required=True)
    return cmd_check(cfg, runner)


def _menu_harden(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.modules.harden import apply_harden, describe_harden, print_plan

    plan = describe_harden(runner)
    print_plan(plan)
    pick = _ask("Select", "number").strip().lower()
    if pick in {str(len(plan.actions) + 1), "b", "back", ""}:
        return 0
    if not pick.isdigit() or not 1 <= int(pick) <= len(plan.actions):
        error("unknown harden action")
        return 2
    action = plan.actions[int(pick) - 1]
    public_key = ""
    user = "root"
    value = ""
    if action.kind == "ssh-key":
        user = _ask("Unix user", "root").strip() or "root"
        public_key = _ask("Public key", "ssh-ed25519 AAAA... comment").strip()
    elif action.takes_value:
        value = _ask(action.value_name or "Value").strip()
    if action.kind in {"run", "flag"} and not args.yes:
        answer = _ask("Run this harden action?", "y/N").strip().lower()
        if answer not in {"y", "yes"}:
            warn("aborted")
            return 0
    try:
        result = apply_harden(
            runner,
            action=action.id,
            public_key=public_key,
            user=user,
            flag=action.flag,
            value=value,
        )
    except ValueError as exc:
        error(str(exc))
        return 2
    if not result.ok:
        error(result.detail)
        return 1
    info(result.detail)
    return 0


def _menu_debug(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_debug

    code = cmd_debug(args, runner)
    if not sys.stdin.isatty():
        return code
    print()
    answer = _ask("Unban one address?", "y/N").strip().lower()
    if answer not in {"y", "yes"}:
        return code
    jail = _ask("fail2ban jail", "sshd").strip() or "sshd"
    address = _ask("IP address").strip()
    from debian_vps_setup.modules.secure_debug import unban_address

    finding = unban_address(runner, address, jail)
    return 0 if finding.status != "fail" else 1


def _menu_troubleshoot(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_troubleshoot, try_load_saved_config

    cfg = try_load_saved_config(args, required=True)
    print()
    print("  0) all")
    for index, name in enumerate(COMPONENTS, start=1):
        print(f"  {index}) {name}")
    pick = _ask("Component", "0 for all").strip().lower()
    if pick in {"", "0", "all"}:
        args.component = "all"
    elif pick.isdigit() and 1 <= int(pick) <= len(COMPONENTS):
        args.component = COMPONENTS[int(pick) - 1]
    elif pick in COMPONENTS:
        args.component = pick
    else:
        error("unknown component")
        return 2
    return cmd_troubleshoot(cfg, args, runner)


def _menu_test(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_test, try_load_saved_config

    cfg = try_load_saved_config(args, required=True)
    return cmd_test(cfg, runner)


def _menu_dns(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_dns, try_load_saved_config

    cfg = try_load_saved_config(args, required=True)
    return cmd_dns(cfg, runner)


def _menu_backup(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.cli import cmd_backup, cmd_restore, try_load_saved_config

    print()
    print("  1) Create snapshot")
    print("  2) Restore snapshot")
    print("  3) Back")
    pick = _ask("Select", "1-3").strip()
    if pick in {"3", "b", "back"}:
        return 0
    if pick == "2":
        return cmd_restore(args, runner)
    cfg = try_load_saved_config(args, required=False)
    return cmd_backup(cfg, args, runner)


def _ask(label: str, hint: str = "") -> str:
    suffix = f" [{hint}]" if hint else ""
    return input(f"{label}{suffix}: ")
