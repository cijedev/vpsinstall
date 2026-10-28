"""Per-component checks and safe auto-fixes for the installer-owned stack."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import heading, info, ok, warn
from debian_vps_setup.modules import ftp, mail, ssl, system, vnc, web, webmail
from debian_vps_setup.modules.backup import create_snapshot
from debian_vps_setup.modules.domain import provision_domain, rewrite_owned as rewrite_domain
from debian_vps_setup.modules.panel import install as install_panel
from debian_vps_setup.modules.verify import Check, COMPONENTS, canonicalize_components, run_checks
from debian_vps_setup.pipeline import select as select_steps
from debian_vps_setup.runner import CommandError, Runner

COMPONENT_SERVICES: dict[str, tuple[str, ...]] = {
    "system": ("fail2ban",),
    "panel": ("webmin", "usermin", "1panel", "1panel-core"),
    "domain": (),
    "web": ("apache2", "nginx"),
    "mail": ("postfix", "dovecot"),
    "ftp": ("proftpd",),
    "ssl": ("apache2", "nginx", "postfix", "dovecot", "proftpd"),
    "webmail": ("apache2", "nginx", "mariadb", "usermin"),
    "vnc": ("vncserver",),
}

REWRITE = {
    "system": system.rewrite_owned,
    "domain": rewrite_domain,
    "web": web.rewrite_owned,
    "mail": mail.rewrite_owned,
    "ftp": ftp.rewrite_owned,
    "webmail": webmail.rewrite_owned,
    "vnc": vnc.rewrite_owned,
}

RERUN = {
    "system": system.configure,
    "panel": install_panel,
    "domain": provision_domain,
    "web": web.configure,
    "mail": mail.configure,
    "ftp": ftp.configure,
    "ssl": ssl.configure,
    "webmail": webmail.configure,
    "vnc": vnc.configure,
}


@dataclass
class FixAction:
    kind: str
    target: str
    detail: str


@dataclass
class ComponentReport:
    component: str
    before: list[Check]
    actions: list[FixAction] = field(default_factory=list)
    after: list[Check] = field(default_factory=list)

    @property
    def failed_before(self) -> list[Check]:
        return [check for check in self.before if not check.passed]

    @property
    def failed_after(self) -> list[Check]:
        return [check for check in (self.after or self.before) if not check.passed]


def planned_actions(component: str, failed: Sequence[Check], cfg: SetupConfig) -> list[FixAction]:
    if not failed:
        return []
    actions: list[FixAction] = []
    for unit in COMPONENT_SERVICES.get(component, ()):
        actions.append(FixAction("restart", unit, f"restart {unit} if it is installed"))
    if component in REWRITE:
        actions.append(
            FixAction(
                "rewrite",
                component,
                "rewrite installer-owned config snippets (no unrelated user data)",
            )
        )
    if component == "ssl":
        actions.append(FixAction("rewrite", "ftp-tls", "re-apply ProFTPD TLS certificate paths"))
    if component in RERUN:
        title = _step_title(component)
        actions.append(FixAction("rerun-step", component, f"re-invoke pipeline step: {title}"))
    if component == "vnc" and not cfg.vnc.enabled:
        return [FixAction("skip", "vnc", "VNC is disabled in config")]
    return actions


def troubleshoot_components(
    cfg: SetupConfig,
    runner: Runner,
    components: Sequence[str] | None = None,
    *,
    dry_run: bool = False,
    apply_fixes: bool = True,
    take_backup: bool = True,
) -> list[ComponentReport]:
    names = canonicalize_components(components)
    reports: list[ComponentReport] = []
    backed_up = False
    for name in names:
        heading(f"Troubleshoot: {name}")
        before = run_checks(cfg, runner, [name])
        report = ComponentReport(component=name, before=before)
        failed = report.failed_before
        if not failed:
            ok(f"{name}: all checks passed")
            report.after = list(before)
            reports.append(report)
            continue
        report.actions = planned_actions(name, failed, cfg)
        if dry_run or not apply_fixes:
            for action in report.actions:
                info(f"[planned] {action.kind}: {action.detail}")
            report.after = list(before)
            reports.append(report)
            continue
        if take_backup and not backed_up:
            create_snapshot(runner, cfg, reason=f"pre-troubleshoot-{name}")
            backed_up = True
        report.after = _apply_fixes(cfg, runner, name, report)
        reports.append(report)
    return reports


def print_reports(reports: list[ComponentReport]) -> int:
    heading("Troubleshoot report")
    any_fail = False
    for report in reports:
        before_fail = ", ".join(check.name for check in report.failed_before) or "none"
        after_fail = ", ".join(check.name for check in report.failed_after) or "none"
        info(f"{report.component}: before failed=[{before_fail}]")
        if report.actions:
            for action in report.actions:
                info(f"  {action.kind}: {action.detail}")
        else:
            info("  no fixes needed")
        info(f"{report.component}: after failed=[{after_fail}]")
        if report.failed_after:
            any_fail = True
            if report.failed_before and {c.name for c in report.failed_after} == {
                c.name for c in report.failed_before
            }:
                warn(f"{report.component}: still failing after attempted fixes")
            else:
                warn(f"{report.component}: remaining failures")
        elif report.failed_before:
            ok(f"{report.component}: recovered")
        else:
            ok(f"{report.component}: healthy")
    return 1 if any_fail else 0


def _apply_fixes(
    cfg: SetupConfig,
    runner: Runner,
    component: str,
    report: ComponentReport,
) -> list[Check]:
    failed = report.failed_before
    if not failed:
        return list(report.before)

    _restart_services(runner, component)
    mid = run_checks(cfg, runner, [component])
    if all(check.passed for check in mid):
        ok(f"{component}: recovered after service restart")
        return mid

    rewriter = REWRITE.get(component)
    if rewriter:
        info(f"rewriting installer-owned {component} config")
        rewriter(cfg, runner)
        if component == "ssl":
            ftp.rewrite_owned(cfg, runner)
        mid = run_checks(cfg, runner, [component])
        if all(check.passed for check in mid):
            ok(f"{component}: recovered after config rewrite")
            return mid
    elif component == "ssl":
        ftp.rewrite_owned(cfg, runner)

    rerun = RERUN.get(component)
    if rerun and (runner.live_system or runner.dry_run):
        info(f"re-invoking pipeline step {component}")
        try:
            rerun(cfg, runner)
        except CommandError as exc:
            warn(str(exc))
        return run_checks(cfg, runner, [component])
    if rerun and not runner.live_system:
        info(f"skipping step re-invoke for {component} (not a live system)")
    return run_checks(cfg, runner, [component])


def _restart_services(runner: Runner, component: str) -> None:
    units = COMPONENT_SERVICES.get(component, ())
    if not units:
        return
    if not runner.live_system:
        for unit in units:
            info(f"[skip restart] {unit} (not a live system)")
        return
    for unit in units:
        info(f"restarting {unit}")
        runner.systemctl("restart", unit, check=False)


def _step_title(component: str) -> str:
    try:
        steps = select_steps([component], None)
    except ValueError:
        return component
    if steps:
        return steps[0][1]
    return component
