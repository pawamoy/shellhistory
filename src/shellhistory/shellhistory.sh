# shellcheck shell=bash
#
# shell-history: record rich shell history for Bash and Zsh.
#
# Each shell provides its own implementation of a small set of primitives:
#
#   _shellhistory_set_command       -> sets _SHELLHISTORY_COMMAND
#   _shellhistory_set_command_type  -> sets _SHELLHISTORY_TYPE
#   _shellhistory_time_now          -> sets _SHELLHISTORY_NOW (microseconds)
#   _shellhistory_can_append        -> returns 0 if the record should be written
#
# They assign to variables rather than writing to stdout on purpose: capturing
# output with $(...) forks a subshell, and these run on every single prompt.

# SHELL-SPECIFIC IMPLEMENTATIONS -----------------------------------------------

if [ -n "${ZSH_VERSION}" ]; then

  # zsh passes the command line to preexec hooks as $1. Reading it from there,
  # rather than from $history[$HISTCMD], is what keeps us correct under
  # HIST_IGNORE_DUPS, HIST_IGNORE_ALL_DUPS, HIST_IGNORE_SPACE, HIST_NO_STORE and
  # HISTORY_IGNORE: none of those advance $HISTCMD, so the old implementation
  # both re-read stale entries and silently dropped commands.
  _shellhistory_set_command() {
    if [ -n "$1" ]; then
      _SHELLHISTORY_COMMAND="$1"
    else
      # Fallback for the (unexpected) case of an empty preexec argument.
      # shellcheck disable=SC2154
      _SHELLHISTORY_COMMAND="${history[$HISTCMD]}"
    fi
  }

  # Honour the user's history-privacy settings. A leading space under
  # HIST_IGNORE_SPACE is the idiomatic way to keep a secret out of the history,
  # so we must not record it either.
  _shellhistory_is_private() {
    if [[ -o hist_ignore_space ]] && [[ "$1" == ' '* || "$1" == $'\t'* ]]; then
      return 0
    fi
    if [[ -n "${HISTORY_IGNORE}" && "$1" == ${~HISTORY_IGNORE} ]]; then
      return 0
    fi
    return 1
  }

  _shellhistory_set_command_type() {
    local type
    _shellhistory_first_word
    type="$(whence -w -- "${_SHELLHISTORY_WORD}" 2>/dev/null)"
    _SHELLHISTORY_TYPE="${type##*: }"
  }

  # In zsh, preexec fires exactly once per command line, so the
  # _SHELLHISTORY_BEFORE_DONE flag alone guarantees one record per prompt cycle.
  # The $HISTCMD comparison Bash needs is not just redundant here, it is harmful:
  # HISTCMD does not advance for entries zsh declines to store.
  _shellhistory_can_append() {
    [ "${_SHELLHISTORY_BEFORE_DONE}" -ne 1 ] && return 1
    [ "${_SHELLHISTORY_PRIVATE}" -eq 1 ] && return 1
    return 0
  }

elif [ -n "${BASH_VERSION}" ]; then

  _shellhistory_set_command() {
    _SHELLHISTORY_COMMAND="$(fc -lnr -0 | sed -e '1s/^\t //')"
  }

  _shellhistory_is_private() {
    case "${HISTCONTROL}" in
      *ignorespace* | *ignoreboth*)
        case "$1" in
          ' '*) return 0 ;;
        esac
        ;;
    esac
    return 1
  }

  _shellhistory_set_command_type() {
    local type
    _shellhistory_first_word
    type="$(type -t "${_SHELLHISTORY_WORD}" 2>/dev/null)"
    _SHELLHISTORY_TYPE="${type}"
  }

  _shellhistory_last_command_number() {
    fc -lr -0 | head -n1 | cut -f1
  }

  # The DEBUG trap fires for every command of a pipeline or list, so Bash does
  # need to compare history numbers to avoid recording the same line twice.
  _shellhistory_can_append() {
    local last_number
    [ "${_SHELLHISTORY_BEFORE_DONE}" -ne 1 ] && return 1
    [ "${_SHELLHISTORY_PRIVATE}" -eq 1 ] && return 1
    last_number="$(_shellhistory_last_command_number)"
    if [ -n "${_SHELLHISTORY_PREVCMD_NUM}" ]; then
      [ "${last_number}" -eq "${_SHELLHISTORY_PREVCMD_NUM}" ] && return 1
    fi
    _SHELLHISTORY_PREVCMD_NUM="${last_number}"
    return 0
  }

fi

# PORTABLE FALLBACKS -----------------------------------------------------------

# Fallback for shells that are neither Bash nor Zsh.
if ! command -v _shellhistory_is_private >/dev/null 2>&1; then
  _shellhistory_is_private() { return 1; }
fi

# HELPERS ----------------------------------------------------------------------

# Sets _SHELLHISTORY_WORD to the first word of the command, ignoring leading
# whitespace. Assigns rather than echoes so callers do not need a $(...) fork.
# FIXME: what about "VAR=value command do something"?
# See https://github.com/Pawamoy/shell-history/issues/13
_shellhistory_first_word() {
  local cmd word
  cmd="${_SHELLHISTORY_COMMAND}"
  word="${cmd#"${cmd%%[![:space:]]*}"}"
  _SHELLHISTORY_WORD="${word%%[[:space:]]*}"
}

# shellcheck disable=SC2120
_shellhistory_parents() {
  local list pid line
  list="$(ps -eo pid,ppid,command | tr -s ' ' | sed 's/^ //g')"
  pid=$$
  while [ "${pid}" -ne 0 ]; do
    line="$(echo "${list}" | grep --text "^${pid} ")"
    echo "${line}" | cut -d' ' -f3-
    pid=$(echo "${line}" | cut -d' ' -f2)
  done
}

_shellhistory_time_now() {
  local now
  now="$(date '+%s%N')"
  _SHELLHISTORY_NOW="${now%???}"
}

_shellhistory_start_timer() {
  if [ -z "${_SHELLHISTORY_START_TIME}" ]; then
    _shellhistory_time_now
    _SHELLHISTORY_START_TIME="${_SHELLHISTORY_NOW}"
  fi
}

_shellhistory_stop_timer() {
  _shellhistory_time_now
  _SHELLHISTORY_STOP_TIME="${_SHELLHISTORY_NOW}"
}

_shellhistory_set_code() {
  _SHELLHISTORY_CODE=$?
}

_shellhistory_set_pwd() {
  _SHELLHISTORY_PWD="${PWD}"
  _SHELLHISTORY_PWD_B64="$(printf '%s' "${PWD}" | base64 -w0)"
}

_shellhistory_append() {
  if _shellhistory_can_append; then
    _shellhistory_append_to_file
  fi
}

_shellhistory_append_to_file() {
  local nl cmd
  nl='
'
  # Continuation lines of a multi-line command are prefixed with ';' so the
  # parser can tell them apart from the ':'-prefixed record header. Done here
  # with a parameter expansion instead of piping through sed: no fork.
  cmd="${_SHELLHISTORY_COMMAND//${nl}/${nl};}"
  printf ':%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s\n' \
    "${_SHELLHISTORY_START_TIME}" \
    "${_SHELLHISTORY_STOP_TIME}" \
    "${_SHELLHISTORY_UUID}" \
    "${_SHELLHISTORY_PARENTS_B64}" \
    "${_SHELLHISTORY_HOSTNAME}" \
    "${USER}" \
    "${_SHELLHISTORY_TTY}" \
    "${_SHELLHISTORY_PWD_B64}" \
    "${SHELL}" \
    "${SHLVL}" \
    "${_SHELLHISTORY_TYPE}" \
    "${_SHELLHISTORY_CODE}" \
    "${cmd}" >> "${SHELLHISTORY_FILE}"
}

_shellhistory_before() {
  [ "${_SHELLHISTORY_BEFORE_DONE}" -gt 0 ] && return

  _shellhistory_set_command "$@"
  if _shellhistory_is_private "${_SHELLHISTORY_COMMAND}"; then
    _SHELLHISTORY_PRIVATE=1
    _SHELLHISTORY_COMMAND=
  else
    _SHELLHISTORY_PRIVATE=0
    _shellhistory_set_command_type
    _shellhistory_set_pwd
  fi
  _shellhistory_start_timer

  _SHELLHISTORY_AFTER_DONE=0
  _SHELLHISTORY_BEFORE_DONE=1
}

_shellhistory_after() {
  _shellhistory_set_code # must always be done first
  _shellhistory_stop_timer

  [ "${_SHELLHISTORY_BEFORE_DONE}" -eq 2 ] && _SHELLHISTORY_BEFORE_DONE=0
  [ "${_SHELLHISTORY_AFTER_DONE}" -eq 1 ] && return

  _shellhistory_append
  _SHELLHISTORY_START_TIME=

  _SHELLHISTORY_BEFORE_DONE=0
  _SHELLHISTORY_AFTER_DONE=1

  return "${_SHELLHISTORY_CODE}"
}

_shellhistory_get_debug_trap() {
  local trap
  trap="$(trap -p | grep ' DEBUG$')" || return 0
  trap=${trap:9}
  trap=${trap:0:-7}
  case ${trap} in
    *';') ;;
    *) trap+=";" ;;
  esac
  echo "${trap}"
}

_shellhistory_enable() {
  _SHELLHISTORY_BEFORE_DONE=2
  _SHELLHISTORY_AFTER_DONE=1
  _SHELLHISTORY_PRIVATE=0
  if [ -n "${ZSH_VERSION}" ]; then
    preexec_functions+=(_shellhistory_before)
    precmd_functions=(_shellhistory_after "${precmd_functions[@]}")
  elif [ -n "${BASH_VERSION}" ]; then
    PROMPT_COMMAND="_shellhistory_after;${PROMPT_COMMAND}"
    # shellcheck disable=SC2064
    trap "$(_shellhistory_get_debug_trap)_shellhistory_before;" DEBUG
  fi
}

_shellhistory_disable() {
  local trap
  local new_prompt
  _SHELLHISTORY_AFTER_DONE=1
  if [ -n "${ZSH_VERSION}" ]; then
    # shellcheck disable=SC2206
    preexec_functions=(${preexec_functions:#_shellhistory_before})
    # shellcheck disable=SC2206
    precmd_functions=(${precmd_functions:#_shellhistory_after})
  elif [ -n "${BASH_VERSION}" ]; then
    trap="$(_shellhistory_get_debug_trap)"
    trap=${trap//_shellhistory_before;}
    new_prompt="${PROMPT_COMMAND//_shellhistory_after;}"
    PROMPT_COMMAND="trap '${trap:--}' DEBUG; PROMPT_COMMAND='${new_prompt}'"
  fi
}

_shellhistory_usage() {
  echo "usage: shellhistory <COMMAND>"
}

_shellhistory_help() {
  _shellhistory_usage
  echo
  echo "Commands:"
  echo "  disable     disable shellhistory"
  echo "  enable      enable shellhistory"
  echo "  help        print this help and exit"
}

# GLOBAL VARIABLES -------------------------------------------------------------
_SHELLHISTORY_CODE=0
_SHELLHISTORY_COMMAND=
_SHELLHISTORY_HOSTNAME="$(hostname)"
_SHELLHISTORY_PARENTS="$(_shellhistory_parents)"
_SHELLHISTORY_PARENTS_B64="$(printf '%s' "${_SHELLHISTORY_PARENTS}" | base64 -w0)"
_SHELLHISTORY_PWD=
_SHELLHISTORY_PWD_B64=
_SHELLHISTORY_START_TIME=
_SHELLHISTORY_STOP_TIME=
_SHELLHISTORY_TTY="$(tty)"
_SHELLHISTORY_TYPE=
_SHELLHISTORY_NOW=
_SHELLHISTORY_WORD=
_SHELLHISTORY_UUID="${_SHELLHISTORY_UUID:-$(uuidgen)}"

_SHELLHISTORY_AFTER_DONE=0
_SHELLHISTORY_BEFORE_DONE=0
_SHELLHISTORY_PRIVATE=0
_SHELLHISTORY_PREVCMD_NUM=

SHELLHISTORY_FILE="${SHELLHISTORY_FILE:-$HOME/.shellhistory/history}"

export SHELLHISTORY_FILE
export _SHELLHISTORY_UUID

# MAIN COMMAND -----------------------------------------------------------------
shellhistory() {
  case "$1" in
    disable) _shellhistory_disable ;;
    enable) _shellhistory_enable ;;
    help) _shellhistory_help ;;
    *)
      _shellhistory_usage >&2
      return 1
      ;;
  esac
}
