# VPS Installer

Debian VPS setup CLI (cije-vps-installer).

## Published from cije.live archive

This repository was published from a cleaned project archive. Secrets, runtime databases, uploads, and dependency folders were removed. Configure via environment variables (see `.env.example` if present, or the docs below).

**Do not commit** `.env`, password files, private keys, or live databases.


Cije VPS Installer
==================

Root-run installer for a **fresh Debian 12 or 13 VPS**. It asks for your domain (and optional Mailbaby SMTP relay), then installs and tests a hosting stack:

- Apache (LAMP) or Nginx (LEMP) plus PHP
- **One** control panel: **Virtualmin + Webmin**, or **1Panel**
- Postfix + Dovecot, with optional outbound mail through [Mailbaby](https://www.mail.baby/)
- Let's Encrypt certificates
- ProFTPD
- Webmail: Roundcube, SnappyMail, Cypht, or Usermin when Virtualmin is selected
- XFCE + TigerVNC so InterServer's desktop/VNC feature can attach to a GUI
- A configuration test pass at the end

Do not run this on an already-configured production server. Use a clean Debian image.

Requirements
------------

- Fresh Debian 12 or 13 (amd64 or arm64)
- Root SSH access
- At least 2 GiB RAM recommended (4 GiB if you choose Virtualmin with spam/virus scanning)
- The domain's A records should point at the VPS before SSL will succeed
- Mailbaby username and password from their portal **only if** you want outbound mail relayed

Quick start
-----------

As root on the VPS:

```bash
chmod +x setup.sh cije-vps-installer
./setup.sh          # interactive menu on a TTY
./setup.sh wizard   # skip the menu and start the prompts
```

Launch prints the Cije VPS Installer ASCII banner. On a terminal with no
subcommand, a numbered **main menu** is shown. `wizard`, `run`, `test`, `dns`,
and `status` still work as before. Non-interactive / CI runs default to `run`.

The wizard asks for:

1. Domain (for example `example.com`) and hostname (`server.example.com`)
2. Site unix user (owns `/home/<user>/public_html`) and whether that user may sudo
3. **Control panel:** `virtualmin` or `1panel`
4. Mailbaby host, port, username, and password (leave username blank to skip the relay)
5. Webmail (`roundcube`, `snappymail`, `cypht`, or `usermin` on Virtualmin), VNC, and Let's Encrypt options

Non-interactive run:

```bash
cp config.example.json /root/vps.json
# edit domain, passwords, control_panel, and optionally mailbaby
chmod 600 /root/vps.json
./setup.sh run --config /root/vps.json --yes
```

On a machine that already has this tree at `~/scripts/vpsinstall`:

```bash
~/scripts/vpsinstall/setup.sh test --config /root/vps.json
```

Pick a control panel
--------------------

Set `control_panel` to exactly one of:

| Value         | What you get |
|---------------|----------------|
| `virtualmin`  | Official Virtualmin GPL installer: Webmin (`:10000`), Usermin (`:20000`), Apache or Nginx, PHP, Postfix, Dovecot, ProFTPD, BIND. `webmin` is accepted as an alias. |
| `1panel`      | [1Panel](https://1panel.pro/) with Docker. Panel URL is `http://IP:PORT/ENTRANCE`. This installer still sets up Apache/Nginx, PHP, mail, FTP, SSL, standalone webmail, and VNC next to it. |

Do **not** install both panels on the same machine.

### Virtualmin + Webmin

- Login: `https://SERVER_IP:10000/` as `root`
- Hosted domain is created as a Virtualmin virtual server
- If the site unix user already exists, the installer removes that account (home is kept) so Virtualmin can recreate it
- Usermin is available if you set `"webmail": "usermin"`
- Roundcube can be installed as the Virtualmin web app and used at `webmail.example.com`

### 1Panel

- Login: `http://SERVER_IP:8888/<security-entrance>` (port and entrance are configurable)
- Username defaults to `vpsadmin`; password defaults to the admin password
- After install, `1pctl user-info` reprints the URL
- Webmail is Roundcube, SnappyMail, or Cypht (Usermin is part of Virtualmin, not 1Panel)
- Keep 1Panel's OpenResty app off ports 80/443 while this installer's Apache/Nginx is serving the site

JSON snippet:

```json
"control_panel": "1panel",
"onepanel": {
  "port": 8888,
  "username": "vpsadmin",
  "password": "",
  "entrance": "",
  "install_dir": "/opt"
}
```

Empty password/entrance are generated for you.

Mailbaby
--------

Mailbaby is optional. Leave `mailbaby.username` and `mailbaby.password` empty to install Postfix/Dovecot without an outbound relay.

When credentials are set, outbound mail is relayed through Mailbaby (`relay.mailbaby.net` port 25 by default, SASL auth, TLS, and the `X-Sender-id` header). Port 587 or 465 is accepted if that is what the portal shows.

Create the SPF (and DKIM) records Mailbaby displays in their portal. A starting SPF is printed by:

```bash
./setup.sh dns --config /root/vps.json
```

Also set the VPS PTR / reverse DNS in InterServer to `server.example.com`.

InterServer VNC desktop
-----------------------

InterServer's **KVM console** is hypervisor VNC and works without extra packages.

This program also installs an **in-guest** XFCE desktop on display `:1` (TCP **5901**) so a GUI desktop is available from a VNC client or a panel that attaches to the guest. Open port 5901 in InterServer's network firewall if the desktop does not connect.

Classic VNC only uses the first eight characters of the password.

Commands
--------

| Command | Purpose |
|---------|---------|
| `./setup.sh` | Interactive menu (TTY only) |
| `./setup.sh menu` | Same menu |
| `./setup.sh wizard` | Prompt, then install |
| `./setup.sh run --config FILE --yes` | Unattended install |
| `./setup.sh run --config FILE --dry-run` | Print actions only |
| `./setup.sh test --config FILE` | Re-test the stack |
| `./setup.sh dns --config FILE` | Print required DNS records |
| `./setup.sh status` | Show completed steps |
| `./setup.sh troubleshoot --config FILE --yes` | Check and auto-fix components |
| `./setup.sh troubleshoot --component mail --dry-run` | Print planned mail fixes |
| `./setup.sh backup` | Snapshot installer-owned config/state |
| `./setup.sh restore --snapshot ID --yes` | Restore that snapshot |
| `./setup.sh run --config FILE --only mail,ssl` | Re-run selected steps |
| `./setup.sh run --config FILE --skip vnc` | Skip a step |
| `./setup.sh run --config FILE --no-backup --yes` | Install without a pre-run snapshot |

`--only virtualmin` and `--only 1panel` both select the control-panel step; which panel is installed still comes from `control_panel` in the config.

The installer is resumable. Completed steps are recorded in `/var/lib/debian-vps-setup/state.json`. Use `--force` to run them again.

Passwords, URLs, and DNS are written to `/root/vps-setup-report.txt` (mode 600). A copy of the config is stored as `/root/vps-setup.json`.

Interactive menu
----------------

The menu is a numbered loop (no extra TUI libraries). Items:

1. Install / resume — wizard, or run from `/root/vps-setup.json`
2. Troubleshoot components — one pipeline step or all
3. Re-test stack — same as `test`
4. Show DNS helper — same as `dns`
5. Status (completed steps)
6. System backup / restore helper
7. Quit

Scripts and CI should keep calling `run` / `test` with `--yes` (and `--config`). stdin that is not a TTY never opens the menu.

Webmail choices
---------------

Set `webmail` to one of:

| Value | Notes |
|-------|--------|
| `roundcube` | Debian packages (or Virtualmin script). Default. |
| `snappymail` | Standalone PHP webmail at `webmail.example.com` and `/snappymail/` |
| `cypht` | Standalone PHP webmail at `webmail.example.com` and `/cypht/` |
| `usermin` | Virtualmin/Webmin only (`:20000`). On 1Panel this is stored as `roundcube`. |

SnappyMail and Cypht work with Virtualmin or 1Panel the same way Roundcube's standalone path does: PHP app + site vhost, not a second control panel.

Troubleshoot
------------

`troubleshoot` loads `/root/vps-setup.json` (or `--config`) and runs the checks from `modules/verify.py` grouped by pipeline component: `system`, `panel`, `domain`, `web`, `mail`, `ftp`, `ssl`, `webmail`, `vnc`.

On failure it:

1. Snapshots installer-owned config (unless `--no-backup`)
2. Restarts the component's services
3. Rewrites snippets this installer already owns
4. Optionally re-invokes that one pipeline step
5. Re-checks and prints a before/after report

`--dry-run` prints the planned fixes only. `--yes` skips the confirmation prompt. It will not wipe mailboxes or unrelated site files.

Config backup and restore
-------------------------

This is **config/state backup**, not full disk imaging. It cannot roll back arbitrary apt packages, mailbox contents, or files you uploaded to the site.

Snapshots live in `/var/lib/debian-vps-setup/backups/<timestamp>/` with a `manifest.json` and copies of installer-owned files, including `/root/vps-setup.json`, `/root/vps-setup-report.txt`, `state.json`, and the `debian-vps-setup` snippets under `/etc`. A package/service inventory is recorded when the tool is running on a live root filesystem.

`run` takes a snapshot automatically unless you pass `--no-backup`. Troubleshoot does the same before auto-fix.

```bash
./setup.sh backup --config /root/vps-setup.json
./setup.sh restore                 # lists snapshots; asks which to apply
./setup.sh restore --snapshot 20260916T080000Z --yes
```

What gets tested
----------------

After install (and whenever you run `test`):

- Web server binary and PHP modules
- Selected control panel (Webmin/Virtualmin `check-config`, or 1Panel/`1pctl`)
- Postfix running, and Mailbaby `relayhost` when credentials are set
- Dovecot IMAP ports
- ProFTPD
- Let's Encrypt certificate (fails until DNS points here — re-run `--only ssl`)
- Roundcube, SnappyMail, Cypht, or Usermin
- VNC port
- Optional test message via `send_test_mail_to`

Local checks (this repository)
------------------------------

```bash
python3 -m unittest discover -s tests -v
```

These tests cover config validation, Mailbaby/Postfix file generation, 1Panel vs Virtualmin command lines, DNS output, webmail choices, backup/restore under `--root`, troubleshoot planning, the TTY menu, and a dry-run of the CLI. They do not install packages.

License
-------

MIT
