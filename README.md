# Shell History

[![ci](https://github.com/pawamoy/shellhistory/workflows/ci/badge.svg)](https://github.com/pawamoy/shellhistory/actions?query=workflow%3Aci)
[![documentation](https://img.shields.io/badge/docs-zensical-FF9100.svg?style=flat)](https://pawamoy.github.io/shellhistory/)
[![pypi version](https://img.shields.io/pypi/v/shellhistory.svg)](https://pypi.org/project/shellhistory/)
[![gitter](https://img.shields.io/badge/matrix-chat-4DB798.svg?style=flat)](https://app.gitter.im/#/room/#shellhistory:gitter.im)

Visualize your usage of Bash/Zsh through a web app thanks to Flask and Highcharts!

## Installation

```bash
pip install shellhistory
```

With [`uv`](https://docs.astral.sh/uv/):

pipx install --python python3.6 shellhistory
```

## Setup

`shellhistory` needs a lot of info to be able to display various charts.
The basic shell history is not enough. In order to generate the necessary
information, you have to enable the shell extension.

At shell startup, in `.bashrc` or `.zshrc`, put the following:

```bash
# only load it for interactive shells
if [[ $- == *i* ]] && command -v shellhistory-location &>/dev/null; then
    . $(shellhistory-location)
    shellhistory enable
fi
```

... and now use your shell normally!

If you want to stop `shellhistory`, simply run `shellhistory disable`.

**Note:** *for performance reasons, you can also use the static,
absolute path to the source file.
Indeed, calling `shellhistory-location` spawns a Python process
which can slow down your shell startup.
Get the path once with `shellhistory-location`, and use `. <ABS_PATH>`.
In my case it's `. ~/.local/pipx/venvs/shellhistory/lib/python3.6/site-packages/shellhistory/shellhistory.sh`.*

## Usage

Launch the web app with `shellhistory-web`.
Now go to [http://localhost:5000/](http://localhost:5000/) and enjoy!

You will need Internet connection since assets are not bundled.

### Filtering and splitting

Every chart is served by the same mechanism, so all of them take the same
query string. Pick one machine, or one terminal, or one project, and every
chart narrows to it; ask for a split, and it comes back as one series per
value of that dimension instead of one:

| Parameter | What it does |
| --- | --- |
| `split=host` | one series per machine — also `user`, `shell`, `level`, `status`, `type` |
| `split=env` | one series per place the shell was typed — also `terminal`, `editor`, `wm`, `multiplexer`, `location`, `display_manager`, `container` |
| `host=corsair` | keep only that value; works for every dimension above |
| `path=/home/me/dev/thing` | keep only the commands run under that directory |
| `cmd=git` | keep only the command lines starting with that |
| `since=2024-01-01`, `until=2024-12-31` | keep only that span |
| `granularity=day` | the bucket of the charts reported over time: `day`, `week`, `month`, `year` |
| `normalize=1` | report each series as a share of its own total, so a busy machine and a quiet one can be compared |

The same controls sit in a bar above every chart, so none of this has to be
typed by hand.

### What the environment charts know

The shell records the whole chain of processes above it, which turns out to
name the terminal emulator, the editor hosting that terminal, the window
manager, the display manager, the multiplexer, and whether an `sshd` sits in
between. None of that is stored a second time: a whole database holds only a
couple of thousand distinct ancestries, so they are read on the fly.

## Some technical info

### How it works

When you enter a command, `shellhistory` will compute values
*before* and *after* the command execution.
In Bash, it uses a trap on DEBUG and the PROMPT_COMMAND variable
(`man bash` for more information).
For Zsh, it uses the preexec_functions and precmd_functions arrays
(anyone knows where to find the official documentation for these?
Some information in `man zshmisc`).

Before the command is executed, we start a timer, compute the command type,
and store the current working directory and the command itself.

After the command has finished, we store the return code, and stop the timer.

### Storage

Records go straight into a SQLite database (`~/.shellhistory/db.sqlite3` by
default, `$SHELLHISTORY_DB` to override). At the end of each command the shell
spawns a small detached writer, `_record.py`, which uses nothing but the standard
library and never makes the prompt wait on it.

### Secret scanning and redaction

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then scan recorded
commands locally without changing them:

```console
shellhistory secrets
```

The report only prints counts, row totals, and rule names: it never prints
commands or detected values. Review the dry-run totals, close the web app and
other processes holding the database open, then apply the redactions:

```console
shellhistory secrets --apply
```

Commands are sent to Gitleaks in batches (500 by default). Provider-specific
Gitleaks rules are extended with rules for shell options, authorization headers,
credentials in URLs, and common short password options. A canary in every batch
makes the scan fail rather than silently marking rows clean when Gitleaks or its
configuration is broken.

Applied scans record each row as `clean` or `redacted` in `secret_scans`; commands
without a record are unscanned. Later scans skip recorded rows, so use
`shellhistory secrets --rescan` (or add `--apply`) after upgrading Gitleaks or
changing scanner rules. Redaction replaces only the extracted value with
`[REDACTED]`; if a finding cannot be mapped back to an exact value, the whole
command is redacted instead.

An applied scan enables SQLite secure deletion, vacuums the database, and
truncates its write-ahead log. Copies and backups made before the scan remain
sensitive, and detected credentials should still be rotated.

The schema has three tables. Everything that stays the same for the whole life
of a shell -- host, user, tty, shell, level and the process ancestry -- is
written once into `sessions`; `history` holds what changes per command (start,
stop, type, return code, working directory, the command itself) and points at
its session; `secret_scans` optionally records the latest scan state for a
history row. The ancestry string alone is a few hundred bytes and there are
only a couple of thousand distinct ones, so repeating it on every row used to
account for half the database file.

Values are passed to the writer as separate arguments and bound as query
parameters, so nothing in a command line can be mistaken for a field separator
or for SQL. That is why no encoding is needed: newlines, colons and quotes are
all stored verbatim.

SQLite is configured for many small concurrent writers -- WAL journaling so
writers do not block readers, and a busy timeout so a shell that loses the race
waits its turn instead of dropping the record. If the database cannot be written
at all, the record is appended to `~/.shellhistory/unrecorded.jsonl` rather than
lost.

#### Legacy text format

Earlier versions appended to a colon-delimited text file, base64-encoding the
paths and ancestry to protect the delimiter, with `;` prefixing continuation
lines of a multi-line command:

```
:start:stop:uuid:parents:host:user:tty:path:shell:level:type:code:command
```

`shellhistory-cli --import [FILE]` still reads that format, so archived history
files can be loaded. A database created by an older version is converted with
`shellhistory-cli --migrate`, which keeps the original alongside as a backup.
### How we get the values

Start and stop time are obtained with `date '+%s%N'`, return code is passed
directly with `$?`, working directory is obtained with `$PWD` and command
type with `type` for Bash and `whence` for Zsh.

Values for UUID, parents, hostname, and TTY are computed only once, when
`shellhistory.sh` is sourced. Indeed they do not change during usage of the current
shell process. Hostname and TTY are obtained through commands `hostname` and
`tty`. UUID is generated with command `uuidgen`. Also note that UUID
is exported in subshells so we know which shell is a subprocess of another, and
so we are able to group shell processes by "sessions", a session being an opened
terminal (be it a tab, window, pane or else). Parents are obtained with a
function that iteratively greps `ps` result with PIDs (see `shellhistory.sh`).

Values for user, shell, and level are simply obtained through environment
variables: `$USER`, `$SHELL` (though its use here is incorrect:
see [issue 24](https://github.com/pawamoy/shellhistory/issues/24)),
and `$SHLVL` (also see [issue 25](https://github.com/pawamoy/shellhistory/issues/25)).

The last command is obtained with the command `fc`.
Using `fc` allows `shellhistory` to have the same behavior as your history:
- if commands starting with spaces are ignored, they will be ignored
  in `shellhistory` as well.
- same for duplicates (entering `ls` two or more times
  saves only the first instance). Note however that if you type the same command
  as the previous one in an other terminal, it will still be appended,
  unless you manage to synchronize your history between terminals,
  which is another story.

Additionally, if you enter an empty line,
or hit Control-C before enter, nothing will be appended either.
The trick behind this is to check the command number in the current history
(see `shellhistory.sh` for technical details).
