"""Interactive numbered TTY menu (stdlib only)."""

from __future__ import annotations

import argparse
import sys

from debian_vps_setup.config import SetupConfig, run_wizard
from debian_vps_setup.console import error, heading, info, warn
from debian_vps_setup.modules.verify import COMPONENTS
from debian_vps_setup.runner import Runner
from debian_vps_setup.state import InstallState

MENU_ITEMS = (
    ("1", "Install / resume"),
    ("2", "Troubleshoot components"),
    ("3", "Re-test stack"),
    ("4", "Show DNS helper"),
    ("5", "Status (completed steps)"),
    ("6", "System backup / restore"),
    ("7", "Quit"),
)


def cmd_menu(args: argparse.Namespace, runner: Runner, state: InstallState) -> int:
    if not sys.stdin.isatty():
        error("menu requires a TTY; use wizard, run, test, dns, status, troubleshoot, backup, or restore")
        return 2
    heading("Main menu")
    info("Choose an action. Non-interactive commands still work as before.")
    while True:
        print()
        for key, label in MENU_ITEMS:
            print(f"  {key}) {label}")
        choice = _ask("Select", "1-7 / q").strip().lower()
        if choice in {"7", "q", "quit"}:
            return 0
        if choice in {"1", "i", "install"}:
            code = _menu_install(args, runner, state)
        elif choice in {"2", "t", "troubleshoot"}:
            code = _menu_troubleshoot(args, runner)
        elif choice in {"3", "test"}:
            code = _menu_test(args, runner)
        elif choice in {"4", "dns"}:
            code = _menu_dns(args, runner)
        elif choice in {"5", "status"}:
            from debian_vps_setup.cli import cmd_status

            code = cmd_status(state)
        elif choice in {"6", "backup", "restore"}:
            code = _menu_backup(args, runner)
        else:
            warn("Enter a number from 1 to 7")
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
