"""Postfix + Dovecot inbound mail, outbound via Mailbaby."""

from __future__ import annotations

from debian_vps_setup.config import Mailbaby, SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import Runner

SASL_PASSWD = "/etc/postfix/sasl_passwd"
HEADER_CUSTOM = "/etc/postfix/header_custom"
DOVECOT_POSTFIX_AUTH = "/etc/dovecot/conf.d/99-debian-vps-setup-postfix-auth.conf"
DOVECOT_MAIL_LAYOUT = "/etc/dovecot/conf.d/99-debian-vps-setup-mail.conf"

# Postfix smtpd (submission) authenticates webmail users through Dovecot,
# the same password database IMAP already uses.
DOVECOT_POSTFIX_AUTH_BODY = """# managed by debian-vps-setup
# Roundcube and other MUAs authenticate on port 587 via this socket.
auth_mechanisms = plain login

service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
"""

# Debian 13 Dovecot defaults to mbox in /var/mail. Virtualmin and this
# installer deliver into ~/Maildir, which is what Usermin reads.
DOVECOT_MAIL_LAYOUT_BODY = """# managed by debian-vps-setup
# Dovecot 2.4 replacement for mail_location = maildir:~/Maildir
mail_driver = maildir
mail_path = ~/Maildir
mail_inbox_path = ~/Maildir
"""


# Postfix smtpd (submission) authenticates webmail users through Dovecot,
# the same password database IMAP already uses.
DOVECOT_POSTFIX_AUTH_BODY = """# managed by debian-vps-setup
# Roundcube and other MUAs authenticate on port 587 via this socket.
auth_mechanisms = plain login

service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
"""



def sasl_passwd_body(mailbaby: Mailbaby) -> str:
    cred = f"{mailbaby.username}:{mailbaby.password}"
    lines = [f"{key} {cred}" for key in mailbaby.sasl_lookup_keys()]
    return "\n".join(lines) + "\n"


def header_custom_body() -> str:
    # Mailbaby uses X-Sender-id from the authenticated sender for reputation.
    return "/^Received: (.*Authenticated sender:)([^)]*)(.*)/i PREPEND X-Sender-id: $2\n"


def postfix_settings(cfg: SetupConfig) -> dict[str, str]:
    mb = cfg.mailbaby
    settings = {
        "relayhost": mb.relayhost(),
        "smtp_sasl_auth_enable": "yes",
        "smtp_sasl_password_maps": f"hash:{SASL_PASSWD}",
        "smtp_sasl_security_options": "noanonymous",
        "smtp_sasl_tls_security_options": "noanonymous",
        "smtp_sasl_mechanism_filter": "plain, login",
        "smtpd_sasl_authenticated_header": "yes",
        "smtp_tls_security_level": "encrypt",
        "smtp_tls_CAfile": "/etc/ssl/certs/ca-certificates.crt",
        "smtp_header_checks": f"regexp:{HEADER_CUSTOM}",
        "smtp_tls_wrappermode": "yes" if mb.port == 465 else "no",
        "myhostname": cfg.hostname,
        "mydomain": cfg.domain,
        # Virtualmin check-config expects myorigin to be the machine hostname.
        "myorigin": cfg.hostname,
    }
    return settings


def configure(cfg: SetupConfig, runner: Runner) -> None:
    if not runner.dry_run:
        runner.run(
            ["debconf-set-selections"],
            input_text=(
                f"postfix postfix/mailname string {cfg.hostname}\n"
                "postfix postfix/main_mailer_type string Internet Site\n"
            ),
            check=False,
        )
    runner.apt_install(
        [
            "postfix",
            "postfix-pcre",
            "dovecot-core",
            "dovecot-imapd",
            "dovecot-pop3d",
            "dovecot-lmtpd",
            "libsasl2-modules",
            "sasl2-bin",
            "swaks",
            "bsd-mailx",
        ]
    )

    runner.write_file(HEADER_CUSTOM, header_custom_body(), mode=0o644)

    if cfg.mailbaby_enabled:
        runner.write_file(SASL_PASSWD, sasl_passwd_body(cfg.mailbaby), mode=0o600)
        if not runner.dry_run:
            mapped = runner.run(["postmap", f"hash:{SASL_PASSWD}"], check=False)
            if mapped.returncode != 0:
                runner.run(["postmap", f"lmdb:{SASL_PASSWD}"], check=False)
                maps = "lmdb:" + SASL_PASSWD
            else:
                maps = "hash:" + SASL_PASSWD
            runner.chmod(SASL_PASSWD, 0o600)
            for suffix in (".db", ".lmdb"):
                db = SASL_PASSWD + suffix
                if runner.exists(db):
                    runner.chmod(db, 0o600)
        else:
            maps = "hash:" + SASL_PASSWD
        settings = postfix_settings(cfg)
        settings["smtp_sasl_password_maps"] = maps
        info(f"Pointing Postfix relayhost at {cfg.mailbaby.relayhost()}")
        for key, value in settings.items():
            runner.run(["postconf", "-e", f"{key} = {value}"])
    else:
        warn("Mailbaby credentials were not provided; local mail is installed without an outbound relay")
        runner.run(["postconf", "-e", f"myhostname = {cfg.hostname}"], check=False)
        runner.run(["postconf", "-e", f"mydomain = {cfg.domain}"], check=False)
        runner.run(["postconf", "-e", f"myorigin = {cfg.hostname}"], check=False)

    # Virtualmin already set virtual mailbox maps; do not wipe them.
    runner.run(["postfix", "check"], check=False)
    runner.systemctl("enable", "postfix", check=False)
    runner.systemctl("restart", "postfix", check=False)
    runner.systemctl("enable", "dovecot", check=False)
    runner.systemctl("restart", "dovecot", check=False)

    if cfg.uses_virtualmin and (runner.which("virtualmin") or runner.exists("/usr/sbin/virtualmin")):
        runner.run(
            ["virtualmin", "modify-mail", "--domain", cfg.domain, "--enable"],
            check=False,
        )
    elif not cfg.uses_virtualmin:
        _configure_standalone_mail(cfg, runner)

    _enable_dovecot_sasl(runner)
    _enable_maildir_layout(runner)
    _compat_dovecot_24(runner)
    runner.systemctl("restart", "dovecot", check=False)
    runner.systemctl("restart", "postfix", check=False)

    ok("mail stack configured" + (" to send through Mailbaby" if cfg.mailbaby_enabled else " (direct delivery until a relay is added)"))
    if cfg.mailbaby_enabled:
        warn("Create the SPF/DKIM records Mailbaby shows in their portal before sending production mail.")


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite Postfix/Dovecot snippets this installer owns (no apt)."""
    runner.write_file(HEADER_CUSTOM, header_custom_body(), mode=0o644)
    if cfg.mailbaby_enabled:
        runner.write_file(SASL_PASSWD, sasl_passwd_body(cfg.mailbaby), mode=0o600)
    runner.write_file(DOVECOT_POSTFIX_AUTH, DOVECOT_POSTFIX_AUTH_BODY)
    runner.write_file(DOVECOT_MAIL_LAYOUT, DOVECOT_MAIL_LAYOUT_BODY)
    if not cfg.uses_virtualmin:
        user = cfg.domain_user
        runner.write_file("/etc/aliases.d/debian-vps-setup", f"admin: {user}\nroot: {user}\n")
        dovecot = """# managed by debian-vps-setup (Dovecot 2.4)
mail_driver = maildir
mail_path = ~/Maildir
auth_allow_cleartext = no
"""
        runner.write_file("/etc/dovecot/conf.d/99-debian-vps-setup.conf", dovecot)


def _enable_dovecot_sasl(runner: Runner) -> None:
    """Point Postfix smtpd at Dovecot so Roundcube SMTP login matches IMAP."""
    info("Configuring Postfix to authenticate mail users through Dovecot")
    runner.write_file(DOVECOT_POSTFIX_AUTH, DOVECOT_POSTFIX_AUTH_BODY)
    if runner.dry_run:
        return
    runner.run(["postconf", "-e", "smtpd_sasl_type = dovecot"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_path = private/auth"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_auth_enable = yes"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_security_options = noanonymous"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_tls_security_options = noanonymous"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_local_domain ="], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_authenticated_header = yes"], check=False)
    runner.run(["postconf", "-e", "smtpd_sasl_mechanism_filter = plain, login"], check=False)
    runner.run(["postconf", "-e", "broken_sasl_auth_clients = yes"], check=False)
    # Submission already has smtpd_sasl_auth_enable; keep TLS optional on
    # localhost so Roundcube can talk to 127.0.0.1:587 without a cert name match.


def _enable_maildir_layout(runner: Runner) -> None:
    """Point IMAP at ~/Maildir so Roundcube sees the same mail as Usermin."""
    info("Pointing Dovecot at ~/Maildir (same store Usermin and Postfix use)")
    runner.write_file(DOVECOT_MAIL_LAYOUT, DOVECOT_MAIL_LAYOUT_BODY)


def _compat_dovecot_24(runner: Runner) -> None:
    """Debian 13 ships Dovecot 2.4, which dropped several 2.3 setting names."""
    conf = runner.resolve("/etc/dovecot/dovecot.conf")
    if conf.exists() and not runner.dry_run:
        text = conf.read_text(encoding="utf-8", errors="replace")
        lines = [
            line
            for line in text.splitlines(True)
            if not line.startswith(
                ("default_mail_env", "disable_plaintext_auth", "mail_location ")
            )
        ]
        conf.write_text("".join(lines), encoding="utf-8")
    pop3 = runner.resolve("/etc/dovecot/conf.d/20-pop3.conf")
    if pop3.exists() and not runner.dry_run:
        body = pop3.read_text(encoding="utf-8", errors="replace")
        body = body.replace(
            "pop3_uidl_format = %08Xu%08Xv",
            "pop3_uidl_format = %{uid | hex(8)}%{uidvalidity | hex(8)}",
        )
        pop3.write_text(body, encoding="utf-8")


def _configure_standalone_mail(cfg: SetupConfig, runner: Runner) -> None:
    """Local delivery for admin@domain when Virtualmin is not managing mailboxes."""
    user = cfg.domain_user
    aliases = f"admin: {user}\nroot: {user}\n"
    runner.write_file("/etc/aliases.d/debian-vps-setup", aliases)
    runner.append_unique("/etc/aliases", f"admin: {user}")
    runner.run(["newaliases"], check=False)
    runner.run(["postconf", "-e", f"mydestination = $myhostname, localhost, localhost.localdomain, {cfg.domain}"])
    runner.run(["postconf", "-e", "home_mailbox = Maildir/"])
    dovecot = """# managed by debian-vps-setup (Dovecot 2.4)
mail_driver = maildir
mail_path = ~/Maildir
auth_allow_cleartext = no
"""
    runner.write_file("/etc/dovecot/conf.d/99-debian-vps-setup.conf", dovecot)
    runner.systemctl("restart", "dovecot", check=False)
    runner.systemctl("restart", "postfix", check=False)
