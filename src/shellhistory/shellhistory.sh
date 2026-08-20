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

  _shellhistory_set_command() {
    # multi-line commands have prepended ';' (starting at line 2)
    # shellcheck disable=SC2154
    _SHELLHISTORY_COMMAND="$(echo "${history[$HISTCMD]}" | sed -e '2,$s/^/;/')"
  }

  _shellhistory_set_command_type() {
    local type
    _shellhistory_first_word
    type="$(whence -w -- "${_SHELLHISTORY_WORD}" 2>/dev/null)"
    _SHELLHISTORY_TYPE="${type##*: }"
  }

  _shellhistory_last_command_number() {
    # shellcheck disable=SC2086
    echo $HISTCMD
  }

elif [ -n "${BASH_VERSION}" ]; then

  _shellhistory_set_command() {
    # multi-line commands have prepended ';' (starting at line 2)
    _SHELLHISTORY_COMMAND="$(fc -lnr -0 | sed -e '1s/^\t //;2,$s/^/;/')"
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

_shellhistory_can_append() {
  local last_number
  [ "${_SHELLHISTORY_BEFORE_DONE}" -ne 1 ] && return 1
  last_number="$(_shellhistory_last_command_number)"
  if [ -n "${_SHELLHISTORY_PREVCMD_NUM}" ]; then
    [ "${last_number}" -eq "${_SHELLHISTORY_PREVCMD_NUM}" ] && return 1
  fi
  _SHELLHISTORY_PREVCMD_NUM="${last_number}"
  return 0
}

_shellhistory_append() {
  if _shellhistory_can_append; then
    _shellhistory_append_to_file
  fi
}

_shellhistory_append_to_file() {
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
    "${_SHELLHISTORY_COMMAND}" >> "${SHELLHISTORY_FILE}"
}

_shellhistory_before() {
  [ "${_SHELLHISTORY_BEFORE_DONE}" -gt 0 ] && return

  _shellhistory_set_command
  _shellhistory_set_command_type
  _shellhistory_set_pwd
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
