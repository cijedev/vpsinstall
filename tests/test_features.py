import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from debian_vps_setup.cli import main
from debian_vps_setup.config import WEBMAIL_CHOICES, load_config
from debian_vps_setup.modules.backup import create_snapshot, list_snapshots, owned_paths, restore_snapshot
from debian_vps_setup.modules.troubleshoot import planned_actions
from debian_vps_setup.modules.verify import Check, COMPONENTS, canonicalize_components, run_checks
from debian_vps_setup.modules.webmail import (
    apache_webmail_vhost,
    cypht_env,
    snappymail_application_ini,
    snappymail_domain_ini,
    webmail_document_root,
)
from debian_vps_setup.report import build_report
from debian_vps_setup.runner import Runner

try:
    from test_config import _minimal, load_config_from_dict
except ImportError:
    from tests.test_config import _minimal, load_config_from_dict


def _write_cfg(**overrides) -> str:
    data = _minimal(**overrides)
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, handle)
    handle.close()
    return handle.name


class WebmailChoiceTests(unittest.TestCase):
    def test_choices_include_snappymail_and_cypht(self):
        self.assertEqual(WEBMAIL_CHOICES, ("roundcube", "usermin", "snappymail", "cypht"))

    def test_snappymail_valid(self):
        cfg = load_config_from_dict(_minimal(webmail="snappymail"))
        self.assertEqual(cfg.webmail, "snappymail")
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(webmail_document_root(cfg), "/var/lib/snappymail")
        self.assertIn("example.com", snappymail_application_ini(cfg))
        self.assertIn("127.0.0.1", snappymail_domain_ini(cfg))
        self.assertIn("/var/lib/snappymail", apache_webmail_vhost(cfg))

    def test_cypht_valid(self):
        cfg = load_config_from_dict(_minimal(webmail="cypht"))
        self.assertEqual(cfg.webmail, "cypht")
        self.assertEqual(cfg.validate(), [])
        self.assertIn("127.0.0.1", cypht_env(cfg))
        self.assertIn("/var/lib/cypht", apache_webmail_vhost(cfg))

    def test_1panel_keeps_snappymail(self):
        cfg = load_config_from_dict(_minimal(control_panel="1panel", webmail="snappymail"))
        self.assertEqual(cfg.webmail, "snappymail")

    def test_1panel_keeps_cypht(self):
        cfg = load_config_from_dict(_minimal(control_panel="1panel", webmail="cypht"))
        self.assertEqual(cfg.webmail, "cypht")

    def test_rejects_unknown_webmail(self):
        cfg = load_config_from_dict(_minimal(webmail="squirrelmail"))
        problems = cfg.validate()
        self.assertTrue(any("webmail" in item for item in problems))

    def test_report_lists_snappymail_url(self):
        cfg = load_config_from_dict(_minimal(webmail="snappymail"))
        text = build_report(cfg, ip="203.0.113.10", results={"webmail": True})
        self.assertIn("Choice: snappymail", text)
        self.assertIn("https://webmail.example.com/", text)
        self.assertIn("https://example.com/snappymail/", text)


class ExampleConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        path = Path(__file__).resolve().parents[1] / "config.example.json"
        cfg = load_config(path)
        self.assertIn(cfg.webmail, WEBMAIL_CHOICES)


class VerifyComponentTests(unittest.TestCase):
    def test_canonicalize_all(self):
        self.assertEqual(canonicalize_components(["all"]), list(COMPONENTS))
        self.assertEqual(canonicalize_components(["mail", "web"]), ["mail", "web"])
        with self.assertRaises(ValueError):
            canonicalize_components(["not-a-step"])

    def test_dry_run_checks_are_grouped(self):
        cfg = load_config_from_dict(_minimal())
        runner = Runner(dry_run=True, root=tempfile.mkdtemp())
        checks = run_checks(cfg, runner, ["mail"])
        self.assertTrue(checks)
        self.assertTrue(all(check.component == "mail" for check in checks))
        self.assertTrue(all(check.passed for check in checks))

    def test_system_and_domain_included_in_full_run(self):
        cfg = load_config_from_dict(_minimal())
        runner = Runner(dry_run=True, root=tempfile.mkdtemp())
        checks = run_checks(cfg, runner)
        components = {check.component for check in checks}
        self.assertIn("system", components)
        self.assertIn("domain", components)
        self.assertIn("webmail", components)


class TroubleshootPlanTests(unittest.TestCase):
    def test_mail_plan_restarts_and_rewrites(self):
        cfg = load_config_from_dict(_minimal())
        failed = [Check("postfix", False, "down", "mail")]
        actions = planned_actions("mail", failed, cfg)
        kinds = [item.kind for item in actions]
        self.assertIn("restart", kinds)
        self.assertIn("rewrite", kinds)
        self.assertIn("rerun-step", kinds)
        self.assertTrue(any(item.target == "postfix" for item in actions))

    def test_no_actions_when_healthy(self):
        cfg = load_config_from_dict(_minimal())
        self.assertEqual(planned_actions("mail", [], cfg), [])


class BackupRestoreTests(unittest.TestCase):
    def test_roundtrip_under_fake_root(self):
        root = tempfile.mkdtemp()
        cfg = load_config_from_dict(_minimal())
        runner = Runner(dry_run=False, root=root)
        hosts = runner.resolve("/etc/hosts")
        hosts.parent.mkdir(parents=True, exist_ok=True)
        hosts.write_text("original-hosts\n", encoding="utf-8")
        saved = runner.resolve("/root/vps-setup.json")
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text('{"domain":"example.com"}\n', encoding="utf-8")
        snap = create_snapshot(runner, cfg, reason="test")
        self.assertTrue((snap.path / "manifest.json").exists())
        self.assertTrue(any(path.endswith("/etc/hosts") or path == "/etc/hosts" for path in owned_paths(cfg)))
        hosts.write_text("changed\n", encoding="utf-8")
        self.assertEqual(restore_snapshot(runner, snap.name), 0)
        self.assertEqual(hosts.read_text(encoding="utf-8"), "original-hosts\n")
        self.assertEqual(list_snapshots(runner)[0].name, snap.name)

    def test_cli_backup_and_restore(self):
        path = _write_cfg()
        root = tempfile.mkdtemp()
        (Path(root) / "etc").mkdir()
        (Path(root) / "etc" / "hostname").write_text("oldname\n", encoding="utf-8")
        (Path(root) / "root").mkdir()
        self.assertEqual(main(["backup", "--config", path, "--root", root, "--yes"]), 0)
        snaps = list((Path(root) / "var/lib/debian-vps-setup/backups").iterdir())
        self.assertTrue(snaps)
        name = snaps[0].name
        (Path(root) / "etc" / "hostname").write_text("mutated\n", encoding="utf-8")
        code = main(["restore", "--root", root, "--snapshot", name, "--yes"])
        self.assertEqual(code, 0)
        self.assertEqual((Path(root) / "etc" / "hostname").read_text(encoding="utf-8"), "oldname\n")


class CliFeatureTests(unittest.TestCase):
    def test_help_lists_new_commands(self):
        buf = StringIO()
        with patch("sys.stdout", buf), self.assertRaises(SystemExit) as raised:
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        out = buf.getvalue()
        self.assertIn("troubleshoot", out)
        self.assertIn("backup", out)
        self.assertIn("menu", out)
        self.assertIn("SnappyMail", out)
        self.assertIn("Cypht", out)

    def test_troubleshoot_dry_run(self):
        path = _write_cfg()
        root = tempfile.mkdtemp()
        code = main(
            [
                "troubleshoot",
                "--config",
                path,
                "--dry-run",
                "--yes",
                "--root",
                root,
                "--component",
                "mail",
            ]
        )
        self.assertEqual(code, 0)

    def test_webmail_cli_choice(self):
        path = _write_cfg(webmail="cypht")
        root = tempfile.mkdtemp()
        code = main(
            [
                "run",
                "--config",
                path,
                "--webmail",
                "snappymail",
                "--dry-run",
                "--yes",
                "--root",
                root,
                "--only",
                "webmail",
                "--no-backup",
            ]
        )
        self.assertEqual(code, 0)

    def test_menu_quit(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="7"):
            self.assertEqual(main(["menu"]), 0)

    def test_menu_rejected_without_tty(self):
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(main(["menu"]), 2)

    def test_tty_default_is_menu(self):
        with patch("sys.stdin.isatty", return_value=True), patch(
            "debian_vps_setup.menu.cmd_menu", return_value=0
        ) as mocked:
            self.assertEqual(main([]), 0)
            mocked.assert_called()

    def test_non_tty_default_is_run(self):
        with patch("sys.stdin.isatty", return_value=False), self.assertRaises(SystemExit) as raised:
            main(["--yes"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
