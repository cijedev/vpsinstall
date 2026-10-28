"""Roundcube, SnappyMail, Cypht, or Usermin webmail."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.modules.virtualmin import virtualmin_present
from debian_vps_setup.runner import Runner

SNAPPYMAIL_URLS = (
    "https://snappymail.eu/repository/latest.tar.gz",
    "https://github.com/the-djmaze/snappymail/releases/latest/download/snappymail-latest.tar.gz",
)
CYPHT_URLS = (
    "https://github.com/cypht-org/cypht/archive/refs/heads/master.tar.gz",
)

SNAPPYMAIL_ROOT = "/var/lib/snappymail"
CYPHT_ROOT = "/var/lib/cypht"
PHP_WEBMAIL_PACKAGES = [
    "php-xml",
    "php-mbstring",
    "php-curl",
    "php-zip",
    "php-gd",
    "php-intl",
    "php-sqlite3",
    "unzip",
]


def roundcube_config_php(cfg: SetupConfig) -> str:
    # Compatible with Roundcube 1.5–1.7. Use loopback submission without
    # STARTTLS so a Let's Encrypt cert for the public name does not fail
    # verification against localhost.
    return f"""<?php
$config['db_dsnw'] = 'mysql://roundcube:{cfg.roundcube_db_password}@localhost/roundcube';
$config['default_host'] = 'localhost:143';
$config['default_port'] = 143;
$config['imap_host'] = 'localhost:143';
$config['smtp_server'] = 'localhost';
$config['smtp_port'] = 587;
$config['smtp_host'] = 'localhost:587';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
$config['smtp_auth_type'] = 'PLAIN';
$config['des_key'] = '{cfg.roundcube_des_key}';
$config['product_name'] = '{cfg.domain} webmail';
$config['plugins'] = ['archive', 'zipdownload', 'managesieve', 'password'];
$config['login_autocomplete'] = 2;
$config['support_url'] = '';
$config['skin'] = 'elastic';
$config['username_domain'] = '{cfg.domain}';
$config['mail_domain'] = '{cfg.domain}';
"""


def apache_webmail_vhost(cfg: SetupConfig, docroot: str | None = None) -> str:
    docroot = docroot or webmail_document_root(cfg)
    return f"""<VirtualHost *:80>
    ServerName {cfg.webmail_host}
    Redirect permanent / https://{cfg.webmail_host}/
</VirtualHost>

<VirtualHost *:443>
    ServerName {cfg.webmail_host}
    DocumentRoot {docroot}

    <Directory {docroot}>
        Options +FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${{APACHE_LOG_DIR}}/webmail-error.log
    CustomLog ${{APACHE_LOG_DIR}}/webmail-access.log combined
</VirtualHost>
"""


def nginx_webmail_server(cfg: SetupConfig, docroot: str | None = None) -> str:
    docroot = docroot or webmail_document_root(cfg)
    return f"""server {{
    listen 80;
    server_name {cfg.webmail_host};
    root {docroot};
    index index.php;
    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}
    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }}
}}
"""


def webmail_document_root(cfg: SetupConfig) -> str:
    if cfg.webmail == "snappymail":
        return SNAPPYMAIL_ROOT
    if cfg.webmail == "cypht":
        return CYPHT_ROOT
    return "/var/lib/roundcube/public_html"


def snappymail_application_ini(cfg: SetupConfig) -> str:
    return f"""[webmail]
title = "{cfg.domain} webmail"
app_path = "/"

[labs]
login_default_domain = "{cfg.domain}"
"""


def snappymail_domain_ini(cfg: SetupConfig) -> str:
    return """imap_host = "127.0.0.1"
imap_port = 143
imap_secure = "None"
smtp_host = "127.0.0.1"
smtp_port = 587
smtp_secure = "None"
smtp_auth = On
smtp_php_mail = Off
"""


def snappymail_domain_json(cfg: SetupConfig) -> str:
    return """{
  "IMAP": {"host": "127.0.0.1", "port": 143, "secure": 0, "shortLogin": false},
  "SMTP": {"host": "127.0.0.1", "port": 587, "secure": 0, "useAuth": true, "usePhpMail": false}
}
"""


def cypht_env(cfg: SetupConfig) -> str:
    return f"""# managed by debian-vps-setup
CYPHT_SESSION_TYPE=PHP
DEFAULT_FOLDERS=Inbox,Drafts,Sent,Trash,Junk
DEFAULT_SETTING_DEFAULT_SMTP_SERVER=127.0.0.1
DEFAULT_SETTING_DEFAULT_SMTP_PORT=587
DEFAULT_SETTING_DEFAULT_SMTP_TLS=false
DEFAULT_SETTING_DEFAULT_IMAP_SERVER=127.0.0.1
DEFAULT_SETTING_DEFAULT_IMAP_PORT=143
DEFAULT_SETTING_DEFAULT_IMAP_TLS=false
DEFAULT_SETTING_DEFAULT_SMTP_NAME={cfg.domain} webmail
"""


def configure(cfg: SetupConfig, runner: Runner) -> None:
    if cfg.webmail == "usermin":
        if not cfg.uses_virtualmin:
            warn("Usermin ships with Virtualmin; installing Roundcube instead")
            _configure_roundcube(cfg, runner)
            return
        _configure_usermin(cfg, runner)
        return
    if cfg.webmail == "snappymail":
        _configure_snappymail(cfg, runner)
        return
    if cfg.webmail == "cypht":
        _configure_cypht(cfg, runner)
        return
    _configure_roundcube(cfg, runner)


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite installer-owned webmail config and vhost snippets (no apt)."""
    if cfg.webmail == "usermin":
        return
    if cfg.webmail == "snappymail":
        _write_snappymail_config(cfg, runner)
        _write_webmail_vhost(cfg, runner, SNAPPYMAIL_ROOT)
        return
    if cfg.webmail == "cypht":
        _write_cypht_config(cfg, runner)
        _write_webmail_vhost(cfg, runner, CYPHT_ROOT)
        return
    runner.write_file(
        "/etc/roundcube/config.inc.php.d/debian-vps-setup.inc.php",
        roundcube_config_php(cfg),
        mode=0o640,
    )
    _write_webmail_vhost(cfg, runner, "/var/lib/roundcube/public_html")


def _configure_usermin(cfg: SetupConfig, runner: Runner) -> None:
    info("Using Usermin for webmail (https://IP:20000 and webmail.domain)")
    runner.systemctl("enable", "usermin", check=False)
    runner.systemctl("start", "usermin", check=False)
    if virtualmin_present(runner) or runner.dry_run:
        runner.run(["virtualmin", "modify-web", "--domain", cfg.domain, "--webmail"], check=False)
    ok("Usermin webmail enabled")


def _configure_roundcube(cfg: SetupConfig, runner: Runner) -> None:
    info("Installing Roundcube webmail")
    use_virtualmin_script = cfg.uses_virtualmin and (
        virtualmin_present(runner) or runner.dry_run
    )
    if use_virtualmin_script:
        installed = runner.run(
            [
                "virtualmin",
                "install-script",
                "--domain",
                cfg.domain,
                "--type",
                "roundcube",
                "--version",
                "latest",
                "--path",
                "/roundcube",
                "--db",
                "mysql roundcube",
                "--newdb",
            ],
            check=False,
        )
        if runner.dry_run or installed.returncode == 0:
            runner.run(["virtualmin", "modify-web", "--no-webmail", "--all-domains"], check=False)
            runner.run(["virtualmin", "modify-web", "--webmail", "--all-domains"], check=False)
            _patch_virtualmin_roundcube_smtp(cfg, runner)
            ok("Roundcube installed through Virtualmin")
            return
        warn("Virtualmin script installer did not install Roundcube; using Debian packages")
        _patch_virtualmin_roundcube_smtp(cfg, runner)

    _install_roundcube_packages(cfg, runner)
    ok("Roundcube installed")


def _configure_snappymail(cfg: SetupConfig, runner: Runner) -> None:
    info("Installing SnappyMail webmail (standalone under the site)")
    runner.apt_install(PHP_WEBMAIL_PACKAGES)
    _download_and_extract(runner, SNAPPYMAIL_URLS, SNAPPYMAIL_ROOT, strip=0)
    _write_snappymail_config(cfg, runner)
    _write_webmail_vhost(cfg, runner, SNAPPYMAIL_ROOT)
    _link_into_site(cfg, runner, "snappymail", SNAPPYMAIL_ROOT)
    if runner.live_system:
        runner.run(["chown", "-R", "www-data:www-data", SNAPPYMAIL_ROOT], check=False)
    ok("SnappyMail installed")


def _configure_cypht(cfg: SetupConfig, runner: Runner) -> None:
    info("Installing Cypht webmail (standalone under the site)")
    runner.apt_install(PHP_WEBMAIL_PACKAGES + ["composer"])
    _download_and_extract(runner, CYPHT_URLS, CYPHT_ROOT, strip=1)
    if runner.dry_run or runner.exists(f"{CYPHT_ROOT}/composer.json"):
        runner.run(
            ["composer", "install", "--no-dev", "--working-dir", CYPHT_ROOT],
            check=False,
        )
    _write_cypht_config(cfg, runner)
    _write_webmail_vhost(cfg, runner, CYPHT_ROOT)
    _link_into_site(cfg, runner, "cypht", CYPHT_ROOT)
    if runner.live_system:
        runner.run(["chown", "-R", "www-data:www-data", CYPHT_ROOT], check=False)
    ok("Cypht installed")


def _write_snappymail_config(cfg: SetupConfig, runner: Runner) -> None:
    base = f"{SNAPPYMAIL_ROOT}/data/_data_/_default_"
    runner.write_file(f"{base}/configs/application.ini", snappymail_application_ini(cfg))
    runner.write_file(f"{base}/domains/{cfg.domain}.ini", snappymail_domain_ini(cfg))
    runner.write_file(f"{base}/domains/{cfg.domain}.json", snappymail_domain_json(cfg))


def _write_cypht_config(cfg: SetupConfig, runner: Runner) -> None:
    runner.write_file(f"{CYPHT_ROOT}/.env", cypht_env(cfg), mode=0o640)


def _download_and_extract(
    runner: Runner,
    urls: tuple[str, ...],
    dest: str,
    *,
    strip: int,
) -> None:
    tmp = "/tmp/debian-vps-setup-webmail.tar.gz"
    runner.run(["mkdir", "-p", dest], check=False)
    fetched = False
    for url in urls:
        result = runner.run(["curl", "-fsSL", "-o", tmp, url], check=False)
        if runner.dry_run or result.returncode == 0:
            fetched = True
            break
        warn(f"download failed: {url}")
    if not fetched and not runner.dry_run:
        warn(f"could not download webmail archive into {dest}; re-run the webmail step")
        return
    cmd = ["tar", "-xzf", tmp, "-C", dest]
    if strip:
        cmd.extend(["--strip-components", str(strip)])
    runner.run(cmd, check=False)


def _write_webmail_vhost(cfg: SetupConfig, runner: Runner, docroot: str) -> None:
    if cfg.web_bundle != "LEMP":
        runner.write_file("/etc/apache2/sites-available/webmail.conf", apache_webmail_vhost(cfg, docroot))
        if runner.live_system:
            runner.run(["a2ensite", "webmail.conf"], check=False)
            runner.systemctl("reload", "apache2", check=False)
        return
    runner.write_file("/etc/nginx/sites-available/webmail", nginx_webmail_server(cfg, docroot))
    if runner.live_system:
        runner.run(
            ["ln", "-sfn", "/etc/nginx/sites-available/webmail", "/etc/nginx/sites-enabled/webmail"],
            check=False,
        )
        runner.systemctl("reload", "nginx", check=False)


def _link_into_site(cfg: SetupConfig, runner: Runner, name: str, dest: str) -> None:
    link = f"{cfg.document_root}/{name}"
    if runner.dry_run:
        info(f"[dry-run] ln -sfn {dest} {link}")
        return
    target = runner.resolve(link)
    target.parent.mkdir(parents=True, exist_ok=True)
    if runner.live_system:
        runner.run(["ln", "-sfn", dest, link], check=False)
        return
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(dest)
    except OSError:
        info(f"could not link {link} -> {dest}")


def _patch_virtualmin_roundcube_smtp(cfg: SetupConfig, runner: Runner) -> None:
    """Virtualmin's Roundcube tree ignores /etc/roundcube; set SMTP there too."""
    marker = "// managed by debian-vps-setup smtp"
    snippet = (
        f"{marker}\n"
        "$config['smtp_host'] = 'localhost:587';\n"
        "$config['smtp_server'] = 'localhost';\n"
        "$config['smtp_port'] = 587;\n"
        "$config['smtp_user'] = '%u';\n"
        "$config['smtp_pass'] = '%p';\n"
        "$config['smtp_auth_type'] = 'PLAIN';\n"
        f"$config['username_domain'] = '{cfg.domain}';\n"
        f"$config['mail_domain'] = '{cfg.domain}';\n"
    )
    paths = [
        f"{cfg.document_root}/roundcube/config/config.inc.php",
        f"{cfg.home_dir}/public_html/roundcube/config/config.inc.php",
    ]
    for path in paths:
        dest = runner.resolve(path)
        if runner.dry_run:
            info(f"[dry-run] patch Roundcube SMTP in {dest}")
            return
        if not dest.exists():
            continue
        text = dest.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            start = text.index(marker)
            text = text[:start].rstrip() + "\n" + snippet
        else:
            text = text.rstrip() + "\n" + snippet
        dest.write_text(text, encoding="utf-8")
        ok(f"patched Roundcube SMTP settings in {dest}")
        return
    warn("Virtualmin Roundcube config.inc.php not found; SMTP defaults were left unchanged")


def _install_roundcube_packages(cfg: SetupConfig, runner: Runner) -> None:
    runner.apt_install(["mariadb-server", "roundcube", "roundcube-core", "roundcube-mysql", "roundcube-plugins"])
    runner.systemctl("enable", "mariadb", check=False)
    runner.systemctl("start", "mariadb", check=False)

    sql_user = cfg.roundcube_db_password.replace("'", "\\'")
    runner.run(
        [
            "mysql",
            "-e",
            "CREATE DATABASE IF NOT EXISTS roundcube CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;",
        ],
        check=False,
    )
    runner.run(
        [
            "mysql",
            "-e",
            f"CREATE USER IF NOT EXISTS 'roundcube'@'localhost' IDENTIFIED BY '{sql_user}'; "
            f"GRANT ALL PRIVILEGES ON roundcube.* TO 'roundcube'@'localhost'; FLUSH PRIVILEGES;",
        ],
        check=False,
    )

    runner.write_file("/etc/roundcube/config.inc.php.d/debian-vps-setup.inc.php", roundcube_config_php(cfg), mode=0o640)
    _write_webmail_vhost(cfg, runner, "/var/lib/roundcube/public_html")
    if cfg.web_bundle != "LEMP" and runner.live_system:
        runner.run(["a2enconf", "roundcube"], check=False)
