# 读取和写回 crontab。读失败时调用方必须停止，不能把失败当成空表。
# 由 install_watchdog.sh 与 rollback_watchdog.sh source。

read_cron_table() {
  CRON_TABLE=""
  CRON_READ_STATUS=""
  CRON_READ_ERROR=""
  if [[ -n "${AUTO_CN_PUSH_CRON_FILE:-}" && -z "${CRONTAB_BIN:-}" ]]; then
    if [[ ! -f "$AUTO_CN_PUSH_CRON_FILE" ]]; then
      CRON_READ_STATUS=empty
      return 0
    fi
    CRON_TABLE="$(cat "$AUTO_CN_PUSH_CRON_FILE")"
    CRON_READ_STATUS=ok
    return 0
  fi
  local bin err rc msg
  bin="${CRONTAB_BIN:-crontab}"
  err="$(mktemp)"
  set +e
  CRON_TABLE="$("$bin" -l 2>"$err")"
  rc=$?
  set -e
  msg="$(cat "$err" 2>/dev/null || true)"
  rm -f "$err"
  if [[ "$rc" -eq 0 ]]; then
    CRON_READ_STATUS=ok
    return 0
  fi
  if printf '%s\n' "$msg" | grep -Eiq 'no crontab for'; then
    CRON_TABLE=""
    CRON_READ_STATUS=empty
    return 0
  fi
  CRON_READ_STATUS=failed
  CRON_READ_ERROR="$msg"
  return 1
}

write_cron_table() {
  local table="$1"
  if [[ -n "${AUTO_CN_PUSH_CRON_FILE:-}" && -z "${CRONTAB_BIN:-}" ]]; then
    printf '%s\n' "$table" > "$AUTO_CN_PUSH_CRON_FILE"
    return 0
  fi
  local bin="${CRONTAB_BIN:-crontab}"
  printf '%s\n' "$table" | "$bin" -
}

cron_preserved() {
  local old="$1"
  local new="$2"
  local marker="$3"
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    [[ "$line" == *"$marker"* ]] && continue
    if ! printf '%s\n' "$new" | grep -Fqx -- "$line"; then
      return 1
    fi
  done <<< "$old"
  return 0
}
