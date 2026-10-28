"""Create the hosted domain: Virtualmin virtual server, or a standalone site."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.modules.system import drop_unix_user_keep_home, grant_sudo
from debian_vps_setup.modules.virtualmin import virtualmin_present
from debian_vps_setup.runner import Runner

PLACEHOLDER_INDEX = """<?php
header('Content-Type: text/html; charset=utf-8');
$domain = {domain!r};
?>
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title><?= htmlspecialchars($domain) ?></title></head>
<body>
<h1><?= htmlspecialchars($domain) ?></h1>
<p>This VPS is serving PHP. Upload the site into this document root
or open the control panel to manage files and applications.</p>
</body></html>
"""


def virtualmin_create_domain_cmd(cfg: SetupConfig) -> list[str]:
    """Virtualmin CLI to create the hosted domain.

    ``--ftp`` is not a valid create-domain flag. ``--unix`` lets Virtualmin
    own the site account; ``--skip-warnings`` lets it reuse an existing home.
    """
    return [
        "virtualmin",
        "create-domain",
        "--domain",
        cfg.domain,
        "--user",
        cfg.domain_user,
        "--pass",
        cfg.admin_password,
        "--desc",
        f"{cfg.domain} (cije-vps-installer)",
        "--email",
        cfg.admin_email,
        "--dir",
        "--webmin",
        "--web",
        "--ssl",
        "--dns",
        "--mail",
        "--mysql",
        "--logrotate",
        "--spam",
        "--virus",
        "--unix",
        "--skip-warnings",
    ]


def provision_domain(cfg: SetupConfig, runner: Runner) -> None:
    if cfg.uses_virtualmin:
        _create_virtual_server(cfg, runner)
        return
    _create_standalone_site(cfg, runner)


def _create_virtual_server(cfg: SetupConfig, runner: Runner) -> None:
    if not virtualmin_present(runner) and not runner.dry_run:
        warn("Virtualmin CLI not found; domain will be created when Virtualmin is available")
        return

    existing = runner.output(["virtualmin", "list-domains", "--name-only"])
    names = {line.strip() for line in existing.splitlines() if line.strip()}
    if cfg.domain in names and not runner.dry_run:
        warn(f"virtual server {cfg.domain} already exists")
    else:
        _prepare_unix_user_for_virtualmin(cfg, runner)
        info(f"Creating virtual server {cfg.domain} owned by {cfg.domain_user}")
        cmd = virtualmin_create_domain_cmd(cfg)
        result = runner.run(cmd, check=False)
        if not runner.dry_run and result.returncode != 0:
            warn("create-domain failed; retrying after dropping a leftover unix user")
            drop_unix_user_keep_home(cfg.domain_user, runner)
            runner.run(cmd, check=False)

    grant_sudo(cfg, runner)

    if cfg.create_admin_mailbox:
        _ensure_virtualmin_mailbox(cfg, runner)
    ok(f"virtual server {cfg.domain} ready")


def _prepare_unix_user_for_virtualmin(cfg: SetupConfig, runner: Runner) -> None:
    """Virtualmin ``create-domain --unix`` fails when the account already exists."""
    if runner.dry_run:
        return
    exists = runner.run(["id", cfg.domain_user], check=False)
    if exists.returncode != 0:
        return
    warn(
        f"unix user {cfg.domain_user} already exists; removing it so Virtualmin "
        "can recreate the account (home directory is kept)"
    )
    drop_unix_user_keep_home(cfg.domain_user, runner)


def _ensure_virtualmin_mailbox(cfg: SetupConfig, runner: Runner) -> None:
    users = runner.output(["virtualmin", "list-users", "--domain", cfg.domain, "--name-only"])
    names = {line.strip() for line in users.splitlines() if line.strip()}
    mailbox = f"admin@{cfg.domain}"
    short = "admin"
    if mailbox in names or short in names:
        info(f"mailbox {mailbox} already exists")
        return
    info(f"Creating mailbox {mailbox}")
    runner.run(
        [
            "virtualmin",
            "create-user",
            "--domain",
            cfg.domain,
            "--user",
            "admin",
            "--pass",
            cfg.admin_password,
            "--real",
            "Administrator",
            "--quota",
            "0",
        ],
        check=False,
    )


def _create_standalone_site(cfg: SetupConfig, runner: Runner) -> None:
    user = cfg.domain_user
    home = cfg.home_dir
    docroot = cfg.document_root
    info(f"Creating site user {user} and document root {docroot}")
    if not runner.dry_run:
        exists = runner.run(["id", user], check=False)
        if exists.returncode != 0:
            runner.run(["useradd", "-m", "-s", "/bin/bash", "-d", home, user], check=False)
        runner.run(
            ["chpasswd"],
            input_text=f"{user}:{cfg.sudo_password or cfg.admin_password}\n",
            check=False,
        )
        runner.run(
            [
                "mkdir",
                "-p",
                docroot,
                f"{home}/Maildir/cur",
                f"{home}/Maildir/new",
                f"{home}/Maildir/tmp",
            ]
        )
    runner.write_file(f"{docroot}/index.php", PLACEHOLDER_INDEX.format(domain=cfg.domain))
    if not runner.dry_run:
        runner.run(["chown", "-R", f"{user}:{user}", home], check=False)
        runner.run(["chmod", "755", home], check=False)
        runner.run(["chmod", "755", docroot], check=False)
    grant_sudo(cfg, runner)
    ok(f"standalone site for {cfg.domain} ready")


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Ensure the document root exists; never overwrite site files."""
    dest = runner.resolve(cfg.document_root)
    if runner.dry_run:
        info(f"[dry-run] mkdir {dest}")
        return
    dest.mkdir(parents=True, exist_ok=True)
