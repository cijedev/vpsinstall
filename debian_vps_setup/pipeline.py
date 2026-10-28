"""Ordered setup steps."""

from __future__ import annotations

from collections.abc import Callable

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.modules import ftp, mail, panel, ssl, system, vnc, web, webmail
from debian_vps_setup.modules.domain import provision_domain
from debian_vps_setup.runner import Runner

StepFn = Callable[[SetupConfig, Runner], None]

STEPS: list[tuple[str, str, StepFn]] = [
    ("system", "Base system, hostname, swap, packages", system.configure),
    ("panel", "Control panel: Virtualmin/Webmin or 1Panel", panel.install),
    ("domain", "Website user, document root, and mailbox", provision_domain),
    ("web", "Web server and PHP extensions", web.configure),
    ("mail", "Postfix/Dovecot and optional Mailbaby relay", mail.configure),
    ("ftp", "ProFTPD", ftp.configure),
    ("ssl", "Let's Encrypt certificates", ssl.configure),
    ("webmail", "Roundcube, SnappyMail, Cypht, or Usermin webmail", webmail.configure),
    ("vnc", "TigerVNC + XFCE for InterServer desktop", vnc.configure),
]

STEP_ALIASES = {
    "virtualmin": "panel",
    "webmin": "panel",
    "1panel": "panel",
    "onepanel": "panel",
}


def names() -> list[str]:
    return [name for name, _, _ in STEPS]


def _canonical(name: str) -> str:
    key = name.strip().lower()
    return STEP_ALIASES.get(key, key)


def select(only: list[str] | None, skip: list[str] | None) -> list[tuple[str, str, StepFn]]:
    chosen = list(STEPS)
    real = set(names())
    if only:
        wanted = {_canonical(item) for item in only if item.strip()}
        unknown = wanted - real
        if unknown:
            raise ValueError(f"unknown --only steps: {', '.join(sorted(unknown))}")
        chosen = [step for step in chosen if step[0] in wanted]
    if skip:
        ignored = {_canonical(item) for item in skip if item.strip()}
        unknown = ignored - real
        if unknown:
            raise ValueError(f"unknown --skip steps: {', '.join(sorted(unknown))}")
        chosen = [step for step in chosen if step[0] not in ignored]
    return chosen
