"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from debian_vps_setup import __version__
from debian_vps_setup.config import (
    WEB_BUNDLES,
    WEBMAIL_CHOICES,
    SetupConfig,
    config_from_args,
    normalize_control_panel,
    run_wizard,
)
from debian_vps_setup.console import APP_NAME, banner, error, heading, info, ok, warn
from debian_vps_setup.dns import render as render_dns
from debian_vps_setup.pipeline import names as step_names
from debian_vps_setup.pipeline import select as select_steps
from debian_vps_setup.preflight import PreflightError, detect_ip, run_preflight
from debian_vps_setup.report import build_report, write_report
from debian_vps_setup.runner import CommandError, Runner
from debian_vps_setup.state import InstallState
from debian_vps_setup.modules.verify import as_result_map, run_checks

LOG_PATH_DEFAULT = "/var/log/debian-vps-setup.log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cije-vps-installer",
        description=(
            f"{APP_NAME}: configure a fresh Debian VPS with a web server, PHP, "
            "1Panel or Virtualmin/Webmin, Postfix/Dovecot with an optional Mailbaby "
            "relay, Let's Encrypt, ProFTPD, Roundcube/SnappyMail/Cypht or Usermin "
            "webmail, and a TigerVNC desktop for InterServer."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        choices=("menu", "wizard", "run", "test", "dns", "status", "troubleshoot", "backup", "restore"),
        help=(
            "menu (default on a TTY), wizard, run (default non-interactive), "
            "test, dns, status, troubleshoot, backup, or restore"
        ),
    )
    parser.add_argument("--config", help="JSON config file (see config.example.json)")
    parser.add_argument("--dry-run", action="store_true", help="print actions without changing the system")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument(
        "--component",
        help="troubleshoot one pipeline component, a comma list, or all (default: all)",
    )
    parser.add_argument("--snapshot", help="backup snapshot id to restore")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not snapshot installer-owned config before run or auto-fix",
    )
    parser.add_argument("--only", help="comma-separated step names to run")
    parser.add_argument("--skip", help="comma-separated step names to skip")
    parser.add_argument("--force", action="store_true", help="re-run steps even if they completed earlier")
    parser.add_argument("--state-dir", default="/var/lib/debian-vps-setup")
    parser.add_argument("--log", default=LOG_PATH_DEFAULT)
    parser.add_argument("--root", default="/", help="filesystem root (tests / dry packaging)")
    parser.add_argument("--domain")
    parser.add_argument("--hostname")
    parser.add_argument("--admin-email")
    parser.add_argument("--admin-password")
    parser.add_argument("--timezone")
    parser.add_argument(
        "--control-panel",
        type=normalize_control_panel,
        help="virtualmin (or webmin) or 1panel",
    )
    parser.add_argument("--web-bundle", choices=WEB_BUNDLES)
    parser.add_argument("--webmail", choices=WEBMAIL_CHOICES)
    parser.add_argument("--mailbaby-host")
    parser.add_argument("--mailbaby-port", type=int)
    parser.add_argument("--mailbaby-username")
    parser.add_argument("--mailbaby-password")
    parser.add_argument("--vnc-password")
    parser.add_argument("--no-vnc", action="store_true")
    parser.add_argument("--no-ssl", action="store_true")
    parser.add_argument("--ssl-staging", action="store_true")
    parser.add_argument("--send-test-mail-to")
    parser.add_argument("--onepanel-port", type=int)
    parser.add_argument("--onepanel-username")
    parser.add_argument("--onepanel-password")
    parser.add_argument("--onepanel-entrance")
    parser.add_argument("--site-user", help="Unix user that owns the site (home is the website account)")
    parser.add_argument("--sudo-password", help="Password for the site sudo user")
    parser.add_argument("--no-sudo", action="store_true", help="do not grant sudo to the site user")
    return parser


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _setup_logging(path: str, root: str) -> None:
    log_path = Path(path)
    if root != "/":
        log_path = Path(root) / "var/log/debian-vps-setup.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    except OSError:
        handlers = [logging.StreamHandler(sys.stderr)]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _confirm(cfg: SetupConfig, args: argparse.Namespace) -> None:
    heading("Plan")
    info(f"Domain:     {cfg.domain}")
    info(f"Hostname:   {cfg.hostname}")
    info(f"Admin:      {cfg.admin_email}")
    info(f"Panel:      {cfg.control_panel}")
    if cfg.uses_1panel:
        info(f"1Panel:     :{cfg.onepanel.port}/{cfg.onepanel.entrance}  user={cfg.onepanel.username}")
    info(f"Site user:  {cfg.domain_user}  home={cfg.home_dir}  docroot={cfg.document_root}")
    info(f"Sudo:       {'yes' if cfg.grant_sudo else 'no'}")
    info(f"Web stack:  {cfg.web_bundle}")
    info(f"Webmail:    {cfg.webmail}")
    info(f"Mailbaby:   {cfg.mailbaby.relayhost()}  user={cfg.mailbaby.username}")
    info(f"SSL:        {'on' if cfg.ssl.enabled else 'off'}")
    info(f"VNC:        {'on, port ' + str(cfg.vnc.port) if cfg.vnc.enabled else 'off'}")
    if args.dry_run:
        warn("dry-run: no packages will be installed")
        return
    if args.yes:
        return
    if not sys.stdin.isatty():
        error("refusing to run non-interactively without --yes")
        raise SystemExit(2)
    answer = input("Proceed with installation? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        raise SystemExit("aborted")


def saved_config_path(root: str) -> Path:
    if root != "/":
        return Path(root) / "root/vps-setup.json"
    return Path("/root/vps-setup.json")


def _config_source(args: argparse.Namespace) -> str | None:
    if args.config:
        return args.config
    saved = saved_config_path(args.root)
    if saved.exists():
        return str(saved)
    return None


def _validated(cfg: SetupConfig) -> SetupConfig:
    problems = cfg.validate()
    if problems:
        for item in problems:
            error(item)
        error("Pass --domain, or --config, or run: cije-vps-installer wizard")
        raise SystemExit(2)
    return cfg


def _load_cfg(args: argparse.Namespace) -> SetupConfig:
    command = args.command or "run"
    if command == "wizard" or (
        command in {"run", None} and not args.config and sys.stdin.isatty() and not args.domain
    ):
        return run_wizard()
    source = _config_source(args)
    if source and not args.config:
        overlay = argparse.Namespace(**vars(args))
        overlay.config = source
        return _validated(config_from_args(overlay))
    return _validated(config_from_args(args))


def try_load_saved_config(args: argparse.Namespace, *, required: bool) -> SetupConfig | None:
    """Load --config, saved /root/vps-setup.json, or CLI identity flags."""
    if args.config or args.domain or saved_config_path(args.root).exists():
        return _load_cfg(args)
    if required:
        error("need --config, --domain, or a saved /root/vps-setup.json")
        raise SystemExit(2)
    return None


def cmd_dns(cfg: SetupConfig, runner: Runner) -> int:
    print(render_dns(cfg, detect_ip(runner)))
    return 0


def cmd_status(state: InstallState) -> int:
    state.load()
    heading("Installer status")
    started = state.data.get("started_at") or "never"
    finished = state.data.get("finished_at") or "no"
    info(f"started:  {started}")
    info(f"finished: {finished}")
    done = state.data.get("completed") or []
    if not done:
        warn("no completed steps")
        return 0
    for name in done:
        ok(name)
    return 0


def cmd_test(cfg: SetupConfig, runner: Runner) -> int:
    checks = run_checks(cfg, runner)
    failed = [check for check in checks if not check.passed]
    heading("Summary")
    info(f"{len(checks) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def cmd_run(cfg: SetupConfig, args: argparse.Namespace, runner: Runner, state: InstallState) -> int:
    _confirm(cfg, args)
    state.load()
    state.mark_started()

    try:
        facts = run_preflight(
            cfg,
            runner,
            require_root=not args.dry_run and args.root == "/",
            enforce_debian=not args.dry_run and args.root == "/",
        )
    except PreflightError:
        return 2

    if not args.no_backup:
        from debian_vps_setup.modules.backup import create_snapshot

        create_snapshot(runner, cfg, reason="pre-run")

    try:
        steps = select_steps(_split_csv(args.only), _split_csv(args.skip))
    except ValueError as exc:
        error(str(exc))
        info("available steps: " + ", ".join(step_names()))
        return 2

    for name, title, fn in steps:
        heading(f"{name}: {title}")
        if state.is_done(name) and not args.force and not args.only:
            warn(f"already completed (use --force to re-run)")
            continue
        try:
            fn(cfg, runner)
        except CommandError as exc:
            error(str(exc))
            error(f"step {name} failed; fix the error and re-run. Completed steps are skipped automatically.")
            return 1
        state.mark_done(name)

    checks = run_checks(cfg, runner)
    results = as_result_map(checks)
    report = build_report(cfg, ip=facts.get("ip") or detect_ip(runner), results=results)
    report_path = write_report(runner, cfg, report)
    state.mark_finished()

    heading("Done")
    print(report)
    ok(f"report saved to {report_path}")
    if any(not passed for passed in results.values()):
        return 1
    return 0


def cmd_troubleshoot(cfg: SetupConfig, args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.modules.troubleshoot import print_reports, troubleshoot_components
    from debian_vps_setup.modules.verify import canonicalize_components

    raw = _split_csv(args.component) or ["all"]
    try:
        components = canonicalize_components(raw)
    except ValueError as exc:
        error(str(exc))
        return 2
    if not args.yes and not args.dry_run and sys.stdin.isatty():
        heading("Troubleshoot")
        info("Components: " + ", ".join(components))
        info("Failed checks get service restarts, then installer-owned config rewrites, then a step re-run.")
        answer = input("Proceed with auto-fix? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("aborted")
    elif not args.yes and not args.dry_run and not sys.stdin.isatty():
        error("refusing to troubleshoot non-interactively without --yes")
        return 2
    reports = troubleshoot_components(
        cfg,
        runner,
        components,
        dry_run=args.dry_run,
        apply_fixes=not args.dry_run,
        take_backup=not args.no_backup,
    )
    return print_reports(reports)


def cmd_backup(cfg: SetupConfig | None, args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.modules.backup import create_snapshot, print_snapshots

    create_snapshot(runner, cfg, reason="manual")
    print_snapshots(runner)
    return 0


def cmd_restore(args: argparse.Namespace, runner: Runner) -> int:
    from debian_vps_setup.modules.backup import BACKUP_NOTE, print_snapshots, restore_snapshot

    snaps = print_snapshots(runner)
    if not snaps:
        return 1
    name = args.snapshot
    if not name:
        if args.yes:
            name = snaps[0].name
            warn(f"--yes with no --snapshot: restoring newest {name}")
        elif sys.stdin.isatty():
            name = input("Snapshot id to restore: ").strip()
        else:
            error("pass --snapshot ID, or run restore on a TTY")
            return 2
    if not name:
        error("no snapshot selected")
        return 2
    if args.dry_run:
        info(f"[dry-run] restore {name}")
        info(BACKUP_NOTE)
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            error("refusing to restore non-interactively without --yes")
            return 2
        answer = input(f"Restore installer-managed files from {name}? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("aborted")
    return restore_snapshot(runner, name)


def resolve_command(args: argparse.Namespace) -> str:
    if args.command:
        return args.command
    if sys.stdin.isatty() and not args.config and not args.domain:
        return "menu"
    return "run"


def main(argv: list[str] | None = None) -> int:
    banner()
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.log, args.root)
    try:
        return _dispatch(parser, args)
    finally:
        logging.shutdown()


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    command = resolve_command(args)
    args.command = command
    runner = Runner(dry_run=args.dry_run, root=args.root)
    state_path = Path(args.state_dir) / "state.json"
    if args.root != "/":
        state_path = Path(args.root) / "var/lib/debian-vps-setup/state.json"
    state = InstallState(state_path)

    if command == "menu":
        from debian_vps_setup.menu import cmd_menu

        return cmd_menu(args, runner, state)
    if command == "status":
        return cmd_status(state)
    if command == "restore":
        return cmd_restore(args, runner)
    if command == "backup":
        cfg = try_load_saved_config(args, required=False)
        return cmd_backup(cfg, args, runner)
    if command == "troubleshoot":
        cfg = _load_cfg(args)
        return cmd_troubleshoot(cfg, args, runner)

    cfg = _load_cfg(args)

    if command == "dns":
        return cmd_dns(cfg, runner)
    if command == "test":
        return cmd_test(cfg, runner)
    if command in {"run", "wizard"}:
        return cmd_run(cfg, args, runner, state)

    parser.error(f"unknown command {command}")
    return 2
