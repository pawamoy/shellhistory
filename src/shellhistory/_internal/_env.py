# SPDX-License-Identifier: ISC
#
# ISC License
#
# Copyright (c) 2020, Timothée Mazzucotelli and contributors
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

# What a session's process ancestry says about where it was typed.
#
# `sessions.parents` holds the full chain of processes above the shell, nearest
# first, one command line per line. That chain names the terminal emulator, the
# editor hosting it, the window manager, the display manager, whether an sshd is
# in the way -- everything the shell itself never bothered to record.
#
# Nothing here is stored: a couple of thousand distinct ancestry strings cover a
# whole database, so classifying them on the fly behind an `lru_cache` costs
# less than the column it would take to keep the answers.

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

UNKNOWN = "unknown"
"""Value used for every axis an ancestry says nothing about."""

# Programs that only carry another program: when one of these leads a line, the
# next word is the process worth naming. `sh -c foo` and friends are excluded by
# the leading-dash test, and a plain `bash` with no argument stays `bash`.
_HOSTS = frozenset({"bash", "dash", "env", "ksh", "node", "perl", "python", "ruby", "sh", "zsh"})

# `python3.11`, `perl5.36`, `node20` and the like all mean the same host.
_VERSIONED = re.compile(r"^(bash|node|perl|python|ruby|sh)[\d.]*$")

TERMINALS = {
    "alacritty": "Alacritty",
    "contour": "Contour",
    "cool-retro-term": "Cool Retro Term",
    "deepin-terminal": "Deepin Terminal",
    "eterm": "Eterm",
    "foot": "foot",
    "ghostty": "Ghostty",
    "gnome-terminal": "GNOME Terminal",
    "gnome-terminal-server": "GNOME Terminal",
    "guake": "Guake",
    "hyper": "Hyper",
    "kgx": "GNOME Console",
    "kitty": "kitty",
    "konsole": "Konsole",
    "lxterminal": "LXTerminal",
    "mate-terminal": "MATE Terminal",
    "mintty": "Mintty",
    "mlterm": "mlterm",
    "qterminal": "QTerminal",
    "rio": "Rio",
    "roxterm": "ROXTerm",
    "rxvt": "rxvt",
    "rxvt-unicode": "urxvt",
    "sakura": "Sakura",
    "st": "st",
    "terminator": "Terminator",
    "terminology": "Terminology",
    "tilda": "Tilda",
    "tilix": "Tilix",
    "urxvt": "urxvt",
    "uxterm": "XTerm",
    "wezterm": "WezTerm",
    "wezterm-gui": "WezTerm",
    "x-terminal-emulator": "x-terminal-emulator",
    "xfce4-terminal": "Xfce Terminal",
    "xterm": "XTerm",
    "yakuake": "Yakuake",
}
"""Terminal emulators, by the name their process goes by."""

# Only the entry points are listed. The helper processes an editor spawns around
# its terminal (`node bootstrap-fork`, `sh server.sh`, `node main.js`) are named
# far too generically to claim here, and they are never the top of the chain
# anyway: skipping them just means the walk keeps going until it reaches the
# editor itself.
EDITORS = {
    "atom": "Atom",
    "clion": "CLion",
    "code": "VS Code",
    "code-insiders": "VS Code Insiders",
    "code-oss": "Code - OSS",
    "code-server": "code-server",
    "codium": "VSCodium",
    "cursor": "Cursor",
    "datagrip": "DataGrip",
    "emacs": "Emacs",
    "goland": "GoLand",
    "helix": "Helix",
    "idea": "IntelliJ IDEA",
    "jupyter-lab": "JupyterLab",
    "jupyter-notebook": "Jupyter Notebook",
    "kate": "Kate",
    "nvim": "Neovim",
    "phpstorm": "PhpStorm",
    "pycharm": "PyCharm",
    "rider": "Rider",
    "rubymine": "RubyMine",
    "sublime_text": "Sublime Text",
    "subl": "Sublime Text",
    "vim": "Vim",
    "webstorm": "WebStorm",
    "windsurf": "Windsurf",
    "zed": "Zed",
    "zed-editor": "Zed",
    "zeditor": "Zed",
}
"""Editors and IDEs that host a terminal of their own."""

WINDOW_MANAGERS = {
    "awesome": "awesome",
    "blackbox": "Blackbox",
    "bspwm": "bspwm",
    "cinnamon": "Cinnamon",
    "dwm": "dwm",
    "enlightenment": "Enlightenment",
    "fluxbox": "Fluxbox",
    "fvwm": "FVWM",
    "gnome-shell": "GNOME Shell",
    "herbstluftwm": "herbstluftwm",
    "hyprland": "Hyprland",
    "i3": "i3",
    "icewm": "IceWM",
    "jwm": "JWM",
    "kwin_wayland": "KWin",
    "kwin_x11": "KWin",
    "marco": "Marco",
    "muffin": "Muffin",
    "mutter": "Mutter",
    "niri": "niri",
    "openbox": "Openbox",
    "plasmashell": "KDE Plasma",
    "qtile": "qtile",
    "ratpoison": "ratpoison",
    "river": "river",
    "spectrwm": "spectrwm",
    "stumpwm": "StumpWM",
    "sway": "Sway",
    "wmii": "wmii",
    "xfwm4": "Xfwm",
    "xmonad": "xmonad",
}
"""Window managers and desktop shells."""

DISPLAY_MANAGERS = {
    "agetty": "console login",
    "gdm": "GDM",
    "gdm3": "GDM",
    "getty": "console login",
    "greetd": "greetd",
    "launchd": "launchd",
    "lightdm": "LightDM",
    "login": "console login",
    "lxdm": "LXDM",
    "sddm": "SDDM",
    "slim": "SLiM",
    "startx": "startx",
    "xdm": "XDM",
    "xinit": "startx",
}
"""How the graphical or text session was started."""

MULTIPLEXERS = {
    "abduco": "abduco",
    "byobu": "Byobu",
    "dtach": "dtach",
    "screen": "GNU Screen",
    "tmux": "tmux",
    "zellij": "Zellij",
}
"""Terminal multiplexers."""

REMOTE = {
    "dropbear": "SSH",
    "mosh-server": "Mosh",
    "sshd": "SSH",
    "telnetd": "telnet",
}
"""Programs that mean the shell is being driven from another machine."""

CONTAINERS = {
    "containerd-shim": "container",
    "containerd-shim-runc-v2": "container",
    "distrobox": "Distrobox",
    "docker": "Docker",
    "lxc-start": "LXC",
    "podman": "Podman",
    "runc": "container",
    "systemd-nspawn": "systemd-nspawn",
    "toolbox": "Toolbox",
}
"""Container runtimes standing between the shell and the host."""

RECORDERS = {
    "asciinema": "asciinema",
    "script": "script",
    "termtosvg": "termtosvg",
    "ttyrec": "ttyrec",
}
"""Session recorders, which host a shell the same way a terminal does."""

# The axes the ancestry is read along, in the order the "context" walk consults
# them: within one process, being an editor beats being a terminal, and both
# beat merely being on the far side of an sshd.
_CONTEXT_TABLES = (EDITORS, TERMINALS, RECORDERS, REMOTE, MULTIPLEXERS, CONTAINERS)

LOCAL = "local"
"""Location of a shell typed at this very machine."""

REMOTE_LOCATION = "remote"
"""Location of a shell reached over the network."""

EDITOR_LOCATION = "editor"
"""Location of a shell embedded in an editor window."""


class Environment(NamedTuple):
    """Everything a process ancestry has to say about one shell session."""

    context: str
    """The nearest ancestor that explains where the shell is being typed."""

    terminal: str
    """The terminal emulator, if any."""

    editor: str
    """The editor or IDE hosting the terminal, if any."""

    window_manager: str
    """The window manager or desktop shell, if any."""

    display_manager: str
    """Whatever started the session: a display manager, startx, a console login."""

    multiplexer: str
    """The terminal multiplexer, if any."""

    container: str
    """The container runtime the shell runs inside, if any."""

    location: str
    """One of `local`, `editor`, `remote`, or `unknown`."""


EMPTY = Environment(*([UNKNOWN] * 8))
"""What an absent or unreadable ancestry classifies to."""

# Axis name -> attribute of Environment. Also the set of environment facets the
# rest of the application is allowed to ask for.
AXES = {
    "env": "context",
    "terminal": "terminal",
    "editor": "editor",
    "wm": "window_manager",
    "display_manager": "display_manager",
    "multiplexer": "multiplexer",
    "container": "container",
    "location": "location",
}
"""The environment axes, mapped to the `Environment` field holding each one."""


def _basename(word: str) -> str:
    """Return the program name in a path, without its directory.

    Parameters:
        word: The first word of a process command line.

    Returns:
        The bare program name, normalized.
    """
    # Deliberately not os.path.basename: a Windows-style ancestry would use
    # backslashes, and stripping both costs nothing.
    name = word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # A login shell is announced as `-bash`; `sshd: user@pts/0` and
    # `tmux: server` label themselves with a trailing colon.
    return name.lstrip("-").rstrip(":")


def _tokens(line: str) -> tuple[str, ...]:
    """Return the names worth matching in one line of an ancestry.

    Parameters:
        line: One process command line from the ancestry.

    Returns:
        The candidate program names, most specific first.
    """
    words = line.split()
    if not words:
        return ()
    first = _basename(words[0])
    versionless = _VERSIONED.sub(r"\1", first)
    # `python /usr/bin/terminator` is terminator, not python -- but `sh -c ...`
    # and a bare `bash` are themselves.
    if versionless in _HOSTS and len(words) > 1 and not words[1].startswith("-"):
        hosted = _basename(words[1])
        if hosted:
            return (hosted, versionless)
    return (versionless,) if versionless == first else (versionless, first)


@lru_cache(maxsize=8192)
def classify(parents: str | None) -> Environment:
    """Read a session's process ancestry.

    A whole database holds only a couple of thousand distinct ancestries, so the
    cache means each one is walked once however many commands point at it.

    Parameters:
        parents: The ancestry string recorded for the session.

    Returns:
        What each axis of the environment was, `unknown` where the ancestry is silent.
    """
    if not parents or parents.isspace():
        return EMPTY

    context = UNKNOWN
    found: dict[int, str] = {}
    for line in parents.splitlines():
        for token in _tokens(line):
            for index, table in enumerate(
                (EDITORS, TERMINALS, WINDOW_MANAGERS, DISPLAY_MANAGERS, MULTIPLEXERS, REMOTE, CONTAINERS, RECORDERS),
            ):
                if token in table and index not in found:
                    found[index] = table[token]
            # The context is the *nearest* ancestor that explains the shell, so
            # the first table to answer as we walk outwards wins and the walk
            # stops asking.
            if context is UNKNOWN:
                for table in _CONTEXT_TABLES:
                    if token in table:
                        context = table[token]
                        break
            if any(token in table for table in _CONTEXT_TABLES):
                break

    editor = found.get(0, UNKNOWN)
    terminal = found.get(1, UNKNOWN)
    remote = found.get(5, UNKNOWN)

    # Being reached over the network is a fact about the machine and outranks
    # whatever window happens to be showing the shell, however deep the sshd is.
    if remote != UNKNOWN:
        location = REMOTE_LOCATION
    elif editor != UNKNOWN:
        location = EDITOR_LOCATION
    elif terminal != UNKNOWN:
        location = LOCAL
    else:
        location = UNKNOWN

    return Environment(
        context=context,
        terminal=terminal,
        editor=editor,
        window_manager=found.get(2, UNKNOWN),
        display_manager=found.get(3, UNKNOWN),
        multiplexer=found.get(4, UNKNOWN),
        container=found.get(6, UNKNOWN),
        location=location,
    )


def axis(parents: str | None, name: str) -> str:
    """Return one axis of the environment described by an ancestry.

    Parameters:
        parents: The ancestry string recorded for the session.
        name: An axis name, as listed in `AXES`.

    Returns:
        The value of that axis.
    """
    return getattr(classify(parents), AXES[name])
