import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from debian_vps_setup.cli import main
from debian_vps_setup.modules.harden import apply_harden, describe_harden, parse_help
from debian_vps_setup.modules.secure_debug import (
    debug_security,
    parse_banned_addresses,
    parse_fail2ban_jails,
    parse_sshd_settings,
    unban_address,
)
from debian_vps_setup.runner import Runner

try:
    from test_config import _minimal
except ImportError:
    from tests.test_config import _minimal

PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqiJsBjAsv4KymedFcUR891X1lgC90DW8yMtjcHJ/p0 cursor"
)

HARDEN_SH = """#!/bin/bash
case "${1:-}" in
  --help|-h)
    cat <<'EOF'
Usage: harden.sh [options]
  -k, --ssh-key KEY    add an SSH public key and then lock passwords
  --port PORT          SSH port to allow
  --no-password        disable SSH password authentication
EOF
    exit 0
    ;;
esac
printf '%s\\n' "$@" >> "$(dirname "$0")/invocations.log"
exit 0
"""


def _plant_harden(root: Path) -> Path:
    folder = root / "root" / "scripts" / "harden"
    folder.mkdir(parents=True)
    script = folder / "harden.sh"
    script.write_text(HARDEN_SH, encoding="utf-8")
    script.chmod(0o755)
    return script


def _write_cfg() -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(_minimal(), handle)
    handle.close()
    return handle.name


class HelpParseTests(unittest.TestCase):
    def test_options_become_actions(self):
        options = parse_help(HARDEN_SH)
        names = [item.name for item in options]
        self.assertIn("ssh-key", names)
        self.assertIn("port", names)
        self.assertIn("no-password", names)
        by_name = {item.name: item for item in options}
        self.assertTrue(by_name["ssh-key"].takes_value)
        self.assertTrue(by_name["port"].takes_value)
        self.assertFalse(by_name["no-password"].takes_value)
        self.assertNotIn("help", names)


class HardenApplyTests(unittest.TestCase):
    def test_missing_script_still_installs_a_key(self):
        root = Path(tempfile.mkdtemp())
        runner = Runner(dry_run=False, root=root)
        plan = describe_harden(runner)
        self.assertFalse(plan.found)
        self.assertEqual([item.kind for item in plan.actions], ["ssh-key"])
        result = apply_harden(runner, action="ssh-key", public_key=PUBLIC_KEY, user="root")
        self.assertTrue(result.ok)
        keys = (root / "root" / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
        self.assertIn("ssh-ed25519", keys)
        self.assertIn("harden script not found", result.detail)

    def test_script_options_and_ssh_key_are_menu_actions(self):
        root = Path(tempfile.mkdtemp())
        script = _plant_harden(root)
        runner = Runner(dry_run=False, root=root)
        plan = describe_harden(runner)
        self.assertTrue(plan.found)
        kinds = [item.kind for item in plan.actions]
        self.assertEqual(kinds[0], "run")
        self.assertIn("ssh-key", kinds)
        flags = [item.flag for item in plan.actions]
        self.assertIn("--port", flags)
        self.assertIn("--no-password", flags)
        self.assertNotIn("--ssh-key", flags)

        result = apply_harden(runner, action="ssh-key", public_key=PUBLIC_KEY, user="root")
        self.assertTrue(result.ok, result.detail)
        logged = (script.parent / "invocations.log").read_text(encoding="utf-8")
        self.assertIn("--ssh-key", logged)
        self.assertIn("ssh-ed25519", logged)

        port = apply_harden(runner, action="flag:port", value="2222")
        self.assertTrue(port.ok, port.detail)
        self.assertEqual(port.command[-2:], ["--port", "2222"])

    def test_rejects_a_private_key(self):
        root = Path(tempfile.mkdtemp())
        runner = Runner(dry_run=False, root=root)
        with self.assertRaises(ValueError):
            apply_harden(
                runner,
                action="ssh-key",
                public_key="-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n",
            )


class DebugTests(unittest.TestCase):
    def test_sshd_first_value_wins_and_typos_fail(self):
        settings, notes = parse_sshd_settings(
            [
                ("/etc/ssh/sshd_config", "PasswordAuthentication no\nPermitRootLogin prohibit-password\n"),
                ("/etc/ssh/sshd_config.d/later.conf", "PasswordAuthentication yes\nPasswordAuthentification yes\n"),
            ]
        )
        self.assertEqual(settings["PasswordAuthentication"], "no")
        self.assertTrue(any(item.status == "fail" and "PasswordAuthentification" in item.detail for item in notes))
        self.assertTrue(any("first value" in item.detail for item in notes))

    def test_fail2ban_parsers(self):
        status = "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, recidive\n"
        self.assertEqual(parse_fail2ban_jails(status), ["sshd", "recidive"])
        jail = "Status for the jail: sshd\n|- Currently banned:\t1\n`- Banned IP list:\t203.0.113.10\n"
        self.assertEqual(parse_banned_addresses(jail), ["203.0.113.10"])

    def test_lockout_when_passwords_off_and_no_key(self):
        root = Path(tempfile.mkdtemp())
        ssh_dir = root / "etc" / "ssh"
        ssh_dir.mkdir(parents=True)
        (ssh_dir / "sshd_config").write_text(
            "Port 22\nPasswordAuthentication no\nPubkeyAuthentication yes\nPermitRootLogin prohibit-password\n",
            encoding="utf-8",
        )
        jail_dir = root / "etc" / "fail2ban" / "jail.d"
        jail_dir.mkdir(parents=True)
        (jail_dir / "debian-vps-setup.conf").write_text("[sshd]\nenabled = true\n", encoding="utf-8")
        report = debug_security(Runner(dry_run=False, root=root))
        names = {item.name: item for item in report.findings}
        self.assertEqual(names["SSH lockout"].status, "fail")
        self.assertFalse(report.ok)
        unban = unban_address(Runner(dry_run=False, root=root), "203.0.113.10", "sshd")
        self.assertEqual(unban.status, "ok")
        self.assertIn("would unban", unban.detail)

    def test_key_clears_the_lockout_finding(self):
        root = Path(tempfile.mkdtemp())
        ssh_dir = root / "etc" / "ssh"
        ssh_dir.mkdir(parents=True)
        (ssh_dir / "sshd_config").write_text("PasswordAuthentication no\nPubkeyAuthentication yes\n", encoding="utf-8")
        home = root / "root" / ".ssh"
        home.mkdir(parents=True)
        home.chmod(0o700)
        keys = home / "authorized_keys"
        keys.write_text(PUBLIC_KEY + "\n", encoding="utf-8")
        keys.chmod(0o600)
        report = debug_security(Runner(dry_run=False, root=root))
        names = {item.name: item for item in report.findings}
        self.assertNotIn("SSH lockout", names)
        self.assertEqual(names["SSH passwords"].status, "ok")


class CliMenuTests(unittest.TestCase):
    def test_check_command_reports_a_saved_config(self):
        path = _write_cfg()
        root = tempfile.mkdtemp()
        code = main(["check", "--config", path, "--root", root, "--dry-run"])
        self.assertEqual(code, 0)

    def test_menu_check_then_quit(self):
        path = _write_cfg()
        root = tempfile.mkdtemp()
        answers = iter(["3", "11"])
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=lambda *_: next(answers)):
            self.assertEqual(main(["menu", "--config", path, "--root", root, "--dry-run", "--yes"]), 0)

    def test_harden_cli_adds_a_key_without_a_menu(self):
        root = tempfile.mkdtemp()
        code = main(
            [
                "harden",
                "--root",
                root,
                "--harden-action",
                "ssh-key",
                "--ssh-public-key",
                PUBLIC_KEY,
                "--ssh-user",
                "root",
            ]
        )
        self.assertEqual(code, 0)
        text = (Path(root) / "root" / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
        self.assertIn(PUBLIC_KEY, text)


if __name__ == "__main__":
    unittest.main()
