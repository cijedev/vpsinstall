"""Ensure the web server is running and PHP extensions are present."""

from __future__ import annotations

import glob

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, ok, warn
from debian_vps_setup.runner import Runner

PHP_BASE = ["php", "php-cli", "php-common", "php-fpm"]


def configure(cfg: SetupConfig, runner: Runner) -> None:
    if cfg.web_bundle == "LEMP":
        runner.apt_install(["nginx", "nginx-full"])
        runner.systemctl("enable", "nginx", check=False)
        runner.systemctl("start", "nginx", check=False)
        if not cfg.uses_virtualmin:
            _write_nginx_site(cfg, runner)
    else:
        runner.apt_install(["apache2", "apache2-utils", "libapache2-mod-fcgid"])
        for mod in ("rewrite", "ssl", "headers", "expires", "deflate", "proxy_fcgi", "http2"):
            runner.run(["a2enmod", mod], check=False)
        runner.systemctl("enable", "apache2", check=False)
        runner.systemctl("start", "apache2", check=False)
        if not cfg.uses_virtualmin:
            _write_apache_site(cfg, runner)

    packages = list(PHP_BASE)
    for ext in cfg.php_extensions:
        ext = ext.strip().lstrip("php-")
        if not ext:
            continue
        if ext in {"cli", "fpm", "common"}:
            continue
        packages.append(f"php-{ext}")
    info("Installing PHP and extensions: " + ", ".join(packages))
    runner.apt_install(packages)

    php_ini_bits = {
        "upload_max_filesize": "64M",
        "post_max_size": "64M",
        "memory_limit": "256M",
        "max_execution_time": "120",
        "date.timezone": cfg.timezone or "UTC",
    }
    snippet = "; managed by debian-vps-setup\n" + "\n".join(
        f"{key} = {value}" for key, value in php_ini_bits.items()
    )
    # Debian stores PHP mods under /etc/php/<version>/mods-available, not /etc/php/conf.d.
    _install_versioned_php_ini(runner, snippet)

    fpm = runner.output("ls /etc/init.d/php*-fpm 2>/dev/null || true")
    if "php" in fpm or runner.exists("/usr/sbin/php-fpm") or runner.which("php-fpm"):
        runner.run("systemctl enable --now php*-fpm 2>/dev/null || true", check=False)

    version = runner.output(["php", "-v"]) if runner.which("php") or runner.dry_run else ""
    if version:
        info(version.splitlines()[0] if version else "PHP installed")
    else:
        warn("php binary not on PATH yet")
    ok("web server and PHP configured")


def rewrite_owned(cfg: SetupConfig, runner: Runner) -> None:
    """Rewrite installer-owned vhosts and PHP snippets (no apt, no service reloads)."""
    if not cfg.uses_virtualmin:
        if cfg.web_bundle == "LEMP":
            runner.write_file(f"/etc/nginx/sites-available/{cfg.domain}", nginx_site_server(cfg))
        else:
            runner.write_file(
                f"/etc/apache2/sites-available/{cfg.domain}.conf",
                apache_site_vhost(cfg),
            )
    php_ini_bits = {
        "upload_max_filesize": "64M",
        "post_max_size": "64M",
        "memory_limit": "256M",
        "max_execution_time": "120",
        "date.timezone": cfg.timezone or "UTC",
    }
    snippet = "; managed by debian-vps-setup\n" + "\n".join(
        f"{key} = {value}" for key, value in php_ini_bits.items()
    )
    runner.write_file("/etc/php/mods-available/debian-vps-setup.ini", snippet + "\n")
    if runner.live_system:
        _install_versioned_php_ini(runner, snippet)


def apache_site_vhost(cfg: SetupConfig) -> str:
    root = cfg.document_root
    return f"""<VirtualHost *:80>
    ServerName {cfg.domain}
    ServerAlias www.{cfg.domain}
    DocumentRoot {root}

    <Directory {root}>
        Options FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${{APACHE_LOG_DIR}}/{cfg.domain}-error.log
    CustomLog ${{APACHE_LOG_DIR}}/{cfg.domain}-access.log combined
</VirtualHost>
"""


def nginx_site_server(cfg: SetupConfig) -> str:
    root = cfg.document_root
    return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {cfg.domain} www.{cfg.domain};
    root {root};
    index index.php index.html;

    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }}
}}
"""


def _write_apache_site(cfg: SetupConfig, runner: Runner) -> None:
    runner.write_file(f"/etc/apache2/sites-available/{cfg.domain}.conf", apache_site_vhost(cfg))
    runner.run(["a2ensite", f"{cfg.domain}.conf"], check=False)
    runner.run(["a2dissite", "000-default.conf"], check=False)
    runner.systemctl("reload", "apache2", check=False)


def _write_nginx_site(cfg: SetupConfig, runner: Runner) -> None:
    runner.write_file(f"/etc/nginx/sites-available/{cfg.domain}", nginx_site_server(cfg))
    if not runner.dry_run:
        runner.run(
            [
                "ln",
                "-sfn",
                f"/etc/nginx/sites-available/{cfg.domain}",
                f"/etc/nginx/sites-enabled/{cfg.domain}",
            ],
            check=False,
        )
    runner.systemctl("reload", "nginx", check=False)


def _install_versioned_php_ini(runner: Runner, snippet: str) -> None:
    if runner.dry_run:
        return
    for confdir in glob.glob("/etc/php/*/mods-available"):
        path = f"{confdir}/debian-vps-setup.ini"
        runner.write_file(path, snippet + "\n")
        version = confdir.split("/")[3]
        runner.run(["phpenmod", "-v", version, "debian-vps-setup"], check=False)
