import json
import tempfile
import unittest
from pathlib import Path

from debian_vps_setup.config import SetupConfig, load_config, normalize_control_panel
from debian_vps_setup.modules.domain import virtualmin_create_domain_cmd
from debian_vps_setup.modules.mail import (
    DOVECOT_MAIL_LAYOUT_BODY,
    DOVECOT_POSTFIX_AUTH_BODY,
    postfix_settings,
    sasl_passwd_body,
)
from debian_vps_setup.modules.onepanel import access_url, install_argv, install_env
from debian_vps_setup.modules.ssl import SERVICES as SSL_SERVICES
from debian_vps_setup.modules.virtualmin import install_argv as virtualmin_argv
from debian_vps_setup.modules.web import apache_site_vhost
from debian_vps_setup.pipeline import names, select


def _minimal(**overrides) -> dict:
    data = {
        "domain": "example.com",
        "hostname": "server.example.com",
        "admin_email": "admin@example.com",
        "admin_password": "AdminPass123456",
        "mailbaby": {
            "host": "relay.mailbaby.net",
            "port": 25,
            "username": "mb_user",
            "password": "mb_pass",
        },
        "vnc": {"enabled": True, "password": "vncpass1"},
    }
    data.update(overrides)
    return data


class ControlPanelTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_control_panel("webmin"), "virtualmin")
        self.assertEqual(normalize_control_panel("Virtualmin/Webmin"), "virtualmin")
        self.assertEqual(normalize_control_panel("one-panel"), "1panel")
        self.assertEqual(normalize_control_panel("1Panel"), "1panel")

    def test_rejects_unknown_panel(self):
        with self.assertRaises(ValueError):
            normalize_control_panel("cpanel")

    def test_virtualmin_default(self):
        cfg = load_config_from_dict(_minimal())
        self.assertTrue(cfg.uses_virtualmin)
        self.assertFalse(cfg.uses_1panel)
        self.assertEqual(cfg.document_root, "/home/example/public_html")

    def test_1panel_switches_usermin_to_roundcube(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(_minimal(control_panel="1panel", webmail="usermin"), handle)
            path = handle.name
        cfg = load_config(path)
        self.assertEqual(cfg.control_panel, "1panel")
        self.assertEqual(cfg.webmail, "roundcube")
        self.assertTrue(cfg.uses_1panel)
        self.assertEqual(cfg.document_root, "/home/example/public_html")
        self.assertGreaterEqual(len(cfg.onepanel.entrance), 3)
        self.assertEqual(cfg.onepanel.password, "AdminPass123456")
        Path(path).unlink()

    def test_1panel_install_command(self):
        cfg = load_config_from_dict(_minimal(control_panel="1panel"))
        argv = install_argv(cfg)
        self.assertIn("--non-interactive", argv)
        self.assertIn("--install-docker", argv)
        self.assertIn(str(cfg.onepanel.port), argv)
        self.assertIn(cfg.onepanel.entrance, argv)
        env = install_env(cfg)
        self.assertEqual(env["PANEL_NON_INTERACTIVE"], "true")
        self.assertEqual(env["PANEL_PASSWORD"], cfg.onepanel.password)
        self.assertIn(cfg.onepanel.entrance, access_url(cfg, "1.2.3.4"))

    def test_virtualmin_argv_ssl_flag(self):
        cfg = load_config_from_dict(_minimal(ssl={"enabled": False, "staging": False}))
        argv = virtualmin_argv(cfg)
        self.assertIn("--no-hostname-ssl", argv)
        self.assertIn("--bundle", argv)
        self.assertIn("LAMP", argv)

    def test_pipeline_aliases(self):
        self.assertIn("panel", names())
        only_v = select(["virtualmin"], None)
        self.assertEqual([name for name, _, _ in only_v], ["panel"])
        only_1 = select(["1panel"], None)
        self.assertEqual([name for name, _, _ in only_1], ["panel"])
        skipped = select(None, ["vnc"])
        self.assertNotIn("vnc", [name for name, _, _ in skipped])

    def test_apache_vhost_contains_domain(self):
        cfg = load_config_from_dict(_minimal(control_panel="1panel"))
        body = apache_site_vhost(cfg)
        self.assertIn("ServerName example.com", body)
        self.assertIn("/home/example/public_html", body)


def load_config_from_dict(data: dict) -> SetupConfig:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(data, handle)
        path = handle.name
    try:
        return load_config(path)
    finally:
        Path(path).unlink()


class MailTests(unittest.TestCase):
    def test_sasl_keys_cover_host_and_port(self):
        cfg = load_config_from_dict(_minimal())
        body = sasl_passwd_body(cfg.mailbaby)
        self.assertIn("relay.mailbaby.net mb_user:mb_pass", body)
        self.assertIn("relay.mailbaby.net:25 mb_user:mb_pass", body)
        settings = postfix_settings(cfg)
        self.assertEqual(settings["relayhost"], "relay.mailbaby.net:25")
        self.assertEqual(settings["smtp_sasl_auth_enable"], "yes")
        self.assertEqual(settings["smtp_tls_wrappermode"], "no")
        self.assertEqual(settings["myorigin"], "server.example.com")
        self.assertEqual(settings["myhostname"], "server.example.com")

    def test_submission_port_uses_brackets(self):
        cfg = load_config_from_dict(
            _minimal(mailbaby={"host": "relay.mailbaby.net", "port": 587, "username": "u", "password": "p"})
        )
        self.assertEqual(cfg.mailbaby.relayhost(), "[relay.mailbaby.net]:587")
        self.assertEqual(postfix_settings(cfg)["smtp_tls_wrappermode"], "no")

    def test_smtps_enables_wrappermode(self):
        cfg = load_config_from_dict(
            _minimal(mailbaby={"host": "relay.mailbaby.net", "port": 465, "username": "u", "password": "p"})
        )
        self.assertEqual(postfix_settings(cfg)["smtp_tls_wrappermode"], "yes")

    def test_missing_mailbaby_is_invalid(self):
        data = _minimal()
        data["mailbaby"]["username"] = "mb_user"
        data["mailbaby"]["password"] = ""
        cfg = load_config_from_dict(data)
        problems = cfg.validate()
        self.assertTrue(any("mailbaby.password" in item for item in problems))

    def test_mailbaby_optional(self):
        data = _minimal()
        data["mailbaby"]["username"] = ""
        data["mailbaby"]["password"] = ""
        cfg = load_config_from_dict(data)
        self.assertFalse(cfg.mailbaby_enabled)
        self.assertEqual(cfg.validate(), [])

    def test_dovecot_postfix_auth_socket(self):
        self.assertIn("/var/spool/postfix/private/auth", DOVECOT_POSTFIX_AUTH_BODY)
        self.assertIn("unix_listener", DOVECOT_POSTFIX_AUTH_BODY)
        self.assertIn("auth_mechanisms = plain login", DOVECOT_POSTFIX_AUTH_BODY)

    def test_dovecot_reads_maildir(self):
        self.assertIn("mail_driver = maildir", DOVECOT_MAIL_LAYOUT_BODY)
        self.assertIn("~/Maildir", DOVECOT_MAIL_LAYOUT_BODY)


class VirtualminDomainTests(unittest.TestCase):
    def test_create_domain_uses_unix_and_skip_warnings(self):
        cfg = load_config_from_dict(_minimal(site_user="cije", domain="cije.live"))
        cmd = virtualmin_create_domain_cmd(cfg)
        self.assertIn("--unix", cmd)
        self.assertIn("--skip-warnings", cmd)
        self.assertNotIn("--ftp", cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], "cije")
        self.assertEqual(cmd[cmd.index("--domain") + 1], "cije.live")

    def test_ssl_service_certs_skip_proftpd(self):
        self.assertNotIn("proftpd", SSL_SERVICES)
        self.assertIn("postfix", SSL_SERVICES)
        self.assertIn("dovecot", SSL_SERVICES)


class DomainValidationTests(unittest.TestCase):
    def test_rejects_hostname_equal_to_domain(self):
        cfg = load_config_from_dict(_minimal(hostname="example.com"))
        problems = cfg.validate()
        self.assertTrue(any("hostname" in item for item in problems))

    def test_strips_www(self):
        cfg = load_config_from_dict(_minimal(domain="www.example.com"))
        self.assertEqual(cfg.domain, "example.com")
        self.assertEqual(cfg.hostname, "server.example.com")

    def test_cije_site_user_home_is_docroot_parent(self):
        cfg = load_config_from_dict(_minimal(domain="cije.live", site_user="cije"))
        self.assertEqual(cfg.domain_user, "cije")
        self.assertEqual(cfg.home_dir, "/home/cije")
        self.assertEqual(cfg.document_root, "/home/cije/public_html")


if __name__ == "__main__":
    unittest.main()
