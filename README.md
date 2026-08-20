# Shell History

[![ci](https://github.com/pawamoy/shell-history/workflows/ci/badge.svg)](https://github.com/pawamoy/shell-history/actions?query=workflow%3Aci)
[![documentation](https://img.shields.io/badge/docs-mkdocs%20material-blue.svg?style=flat)](https://pawamoy.github.io/shell-history/)
[![pypi version](https://img.shields.io/pypi/v/shell-history.svg)](https://pypi.org/project/shell-history/)

Inspired by [bamos/zsh-history-analysis](https://github.com/bamos/zsh-history-analysis).

Visualize your usage of Bash/Zsh through a web app
thanks to [Flask](http://flask.pocoo.org/) and [Highcharts](https://www.highcharts.com/)!

<table>
  <tr align="center">
    <td>Duration<img alt="duration chart" src="pictures/duration.png" /></td>
    <td>Length<img alt="length chart" src="pictures/length.png" /></td>
    <td>Type<img alt="type chart" src="pictures/type.png" /></td>
  </tr>
  <tr align="center">
    <td>Exit code<img alt="exit code chart" src="pictures/exit_code.png" /></td>
    <td>Hourly<img alt="hourly chart" src="pictures/avg_hourly.png" /></td>
    <td>Daily<img alt="daily chart" src="pictures/avg_daily.png" /></td>
  </tr>
  <tr align="center">
    <td>Over time<img alt="over time chart" src="pictures/over_time.png" /></td>
    <td>Markov chain<img alt="markov chart" src="pictures/markov.png" /></td>
    <td>Top commands<img alt="top chart" src="pictures/top.png" /></td>
  </tr>
</table>

<p align="center"><i>Post your charts ideas in <a href="https://github.com/pawamoy/shell-history/issues/9">this issue</a>!</i></p>

- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Usage](#usage)
- [Some technical info](#some-technical-info)
  - [How it works](#how-it-works)
  - [Storage](#storage)
  - [How we get the values](#how-we-get-the-values)
- [License](#license)

## Requirements

Shell History requires Python 3.6 or above.

<details>
<summary>To install Python 3.6, I recommend using <a href="https://github.com/pyenv/pyenv"><code>pyenv</code></a>.</summary>

```bash
# install pyenv
git clone https://github.com/pyenv/pyenv ~/.pyenv

# setup pyenv (you should also put these three lines in .bashrc or similar)
export PATH="${HOME}/.pyenv/bin:${PATH}"
export PYENV_ROOT="${HOME}/.pyenv"
eval "$(pyenv init -)"

# install Python 3.6
pyenv install 3.6.12

# make it available globally
pyenv global system 3.6.12
```
</details>

## Installation

With `pip`:
```bash
python3.6 -m pip install shellhistory
```

With [`pipx`](https://github.com/pipxproject/pipx):
```bash
python3.6 -m pip install --user pipx

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
spawns a small detached writer, `record.py`, which uses nothing but the standard
library and never makes the prompt wait on it.

The schema has two tables. Everything that stays the same for the whole life of
a shell -- host, user, tty, shell, level and the process ancestry -- is written
once into `sessions`; `history` holds what changes per command (start, stop,
type, return code, working directory, the command itself) and points at its
session. The ancestry string alone is a few hundred bytes and there are only a
couple of thousand distinct ones, so repeating it on every row used to account
for half the database file.

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
see [issue 24](https://github.com/pawamoy/shell-history/issues/24)),
and `$SHLVL` (also see [issue 25](https://github.com/pawamoy/shell-history/issues/25)).

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
