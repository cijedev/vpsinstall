"""Terminal output helpers (stdlib only)."""

from __future__ import annotations

import os
import sys

APP_NAME = "Cije VPS Installer"

# Standard FIGlet "standard" font for CIJE, plus a subtitle.
BANNER_ART = r"""
   ____   ___      _   _____
  / ___| |_ _|    | | | ____|
 | |      | |  _  | | |  _|
 | |___   | | | |_| | | |___
  \____| |___| \___/  |_____|

              VPS INSTALLER
"""


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


class Color:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    red = "\033[31m"
    green = "\033[32m"
    yellow = "\033[33m"
    blue = "\033[34m"
    cyan = "\033[36m"
    magenta = "\033[35m"

    def wrap(self, code: str, text: str) -> str:
        if not _use_color():
            return text
        return f"{code}{text}{self.reset}"


C = Color()


def info(msg: str) -> None:
    print(C.wrap(C.cyan, "→ ") + msg, flush=True)


def ok(msg: str) -> None:
    print(C.wrap(C.green, "✓ ") + msg, flush=True)


def warn(msg: str) -> None:
    print(C.wrap(C.yellow, "! ") + msg, flush=True)


def error(msg: str) -> None:
    print(C.wrap(C.red, "✗ ") + msg, file=sys.stderr, flush=True)


def heading(msg: str) -> None:
    print()
    print(C.wrap(C.bold, msg), flush=True)
    print(C.wrap(C.dim, "─" * min(72, max(12, len(msg)))), flush=True)


def banner_text() -> str:
    return BANNER_ART.strip("\n")


def banner() -> None:
    art = banner_text()
    print()
    print(C.wrap(C.bold + C.cyan, art), flush=True)
    print(
        C.wrap(
            C.dim,
            "  web · mail · Virtualmin or 1Panel · SSL · FTP · webmail · VNC",
        ),
        flush=True,
    )
    print()
