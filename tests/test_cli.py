import json
import tempfile
import unittest
from pathlib import Path

from debian_vps_setup.cli import main
from debian_vps_setup.config import load_config
from debian_vps_setup.console import APP_NAME, banner_text
from debian_vps_setup.dns import render
from debian_vps_setup.modules.vnc import systemd_unit, xstartup_body
from debian_vps_setup.report import build_report


def _write_cfg(control_panel: str = "virtualmin") -> str:
    data = {
        "domain": "example.com",
        "hostname": "server.example.com",
        "admin_email": "admin@example.com",
        "admin_password": "AdminPass123456",
        "control_panel": control_panel,
        "mailbaby": {
            "host": "relay.mailbaby.net",
            "port": 25,
            "username": "mb_user",
            "password": "mb_pass",
        },
        "vnc": {"enabled": True, "password": "vncpass1"},
        "ssl": {"enabled": False, "staging": False},
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, handle)
    handle.close()
    return handle.name


class DnsTests(unittest.TestCase):
    def test_records_include_mx_and_spf(self):
        cfg = load_config(_write_cfg())
        text = render(cfg, "203.0.113.10")
        self.assertIn("203.0.113.10", text)
        self.assertIn("MX", text)
        self.assertIn("include:relay.mailbaby.net", text)
        self.assertIn("mail.example.com", text)

    def test_spf_without_mailbaby(self):
        path = _write_cfg()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["mailbaby"]["username"] = ""
        data["mailbaby"]["password"] = ""
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        cfg = load_config(path)
        text = render(cfg, "203.0.113.10")
        self.assertIn("v=spf1 mx a ~all", text)
        self.assertNotIn("include:relay.mailbaby.net", text)


class VncTests(unittest.TestCase):
    def test_unit_and_xstartup(self):
        cfg = load_config(_write_cfg())
        unit = systemd_unit(cfg)
        self.assertIn(":1", unit)
        self.assertIn("-localhost no", unit)
        self.assertIn("startxfce4", xstartup_body())


class ReportTests(unittest.TestCase):
    def test_virtualmin_report(self):
        cfg = load_config(_write_cfg("virtualmin"))
        text = build_report(cfg, ip="203.0.113.10", results={"PHP": True})
        self.assertIn("Virtualmin", text)
        self.assertIn(":10000", text)
        self.assertIn("Cije VPS Installer report", text)

    def test_1panel_report(self):
        cfg = load_config(_write_cfg("1panel"))
        text = build_report(cfg, ip="203.0.113.10", results={"1Panel": True})
        self.assertIn("1Panel", text)
        self.assertIn(f":{cfg.onepanel.port}/", text)
        self.assertNotIn(":10000", text)


class BannerTests(unittest.TestCase):
    def test_art_names_the_app(self):
        art = banner_text()
        self.assertEqual(APP_NAME, "Cije VPS Installer")
        self.assertIn("VPS INSTALLER", art)
        self.assertIn("___", art)
        self.assertIn("_____", art)

    def test_help_prints_ascii_banner(self):
        from io import StringIO
        from unittest.mock import patch

        buf = StringIO()
        with patch("sys.stdout", buf), self.assertRaises(SystemExit) as raised:
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        out = buf.getvalue()
        self.assertIn("VPS INSTALLER", out)
        self.assertIn("cije-vps-installer", out)


class CliTests(unittest.TestCase):

    def test_dns_command(self):
        path = _write_cfg()
        self.assertEqual(main(["--config", path, "dns"]), 0)
        self.assertEqual(main(["dns", "--config", path]), 0)

    def test_dry_run_1panel(self):
        path = _write_cfg("1panel")
        root = tempfile.mkdtemp()
        code = main(
            [
                "--config",
                path,
                "--dry-run",
                "--yes",
                "--root",
                root,
                "--only",
                "panel",
                "run",
            ]
        )
        self.assertEqual(code, 0)

    def test_missing_domain_fails(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--yes", "--mailbaby-username", "x", "--mailbaby-password", "y", "run"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
