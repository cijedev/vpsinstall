"""DNS records the operator must create for web, mail, and SSL."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig


def public_ip_hint(ip: str | None) -> str:
    return ip or "<your-vps-ip>"


def records(cfg: SetupConfig, ip: str | None = None) -> list[tuple[str, str, str]]:
    target = public_ip_hint(ip)
    if cfg.mailbaby_enabled:
        spf = "v=spf1 include:relay.mailbaby.net ~all"
    else:
        spf = "v=spf1 mx a ~all"
    return [
        ("A", "@", target),
        ("A", "www", target),
        ("A", "mail", target),
        ("A", "webmail", target),
        ("A", "ftp", target),
        ("A", cfg.hostname.split(".")[0], target),
        ("MX", "@", f"10 mail.{cfg.domain}."),
        ("TXT", "@", spf),
        (
            "TXT",
            "_dmarc",
            f"v=DMARC1; p=none; rua=mailto:{cfg.admin_email}",
        ),
        ("CNAME", "autoconfig", f"mail.{cfg.domain}."),
        ("CNAME", "autodiscover", f"mail.{cfg.domain}."),
    ]


def render(cfg: SetupConfig, ip: str | None = None) -> str:
    rows = records(cfg, ip)
    width = max(len(name) for _, name, _ in rows)
    lines = [
        f"Create these records at the DNS host for {cfg.domain}.",
        "Let's Encrypt and inbound mail will fail until A/MX records point here.",
        "If you use Mailbaby, copy their DKIM/SPF from the portal if they differ.",
        "",
    ]
    for rtype, name, value in rows:
        lines.append(f"  {rtype:<6} {name:<{width}}  {value}")
    lines.extend(
        [
            "",
            "Also set the VPS reverse DNS (PTR) to "
            f"{cfg.hostname} in the InterServer control panel.",
            "If Mailbaby is configured, prefer their DKIM keys over a second local signer.",
        ]
    )
    return "\n".join(lines)
