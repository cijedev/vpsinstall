"""Install the chosen control panel: Virtualmin/Webmin or 1Panel."""

from __future__ import annotations

from debian_vps_setup.config import SetupConfig
from debian_vps_setup.console import info, warn
from debian_vps_setup.modules import onepanel, virtualmin
from debian_vps_setup.runner import Runner


def install(cfg: SetupConfig, runner: Runner) -> None:
    if cfg.uses_1panel:
        info("Control panel: 1Panel")
        onepanel.install(cfg, runner)
        return
    if cfg.uses_virtualmin:
        info("Control panel: Virtualmin + Webmin")
        virtualmin.install(cfg, runner)
        return
    warn("No control panel selected")
