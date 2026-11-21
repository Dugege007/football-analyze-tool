# 循环、看门狗和安装脚本共用。配置文件在仓库外。
# 调用：load_push_env "$HOME_DIR"；失败时已经写了告警。

load_push_env() {
  local home="$1"
  local env_file="${AUTO_CN_PUSH_ENV:-}"
  mkdir -p "$home/markers" "$home/logs"
  local alert="$home/markers/ALERT_CONFIG.json"
  if [[ -z "$env_file" || ! -f "$env_file" ]]; then
    printf '%s\n' '{"kind":"config","reason":"config_missing","note":"AUTO_CN_PUSH_ENV 未设置或不存在，拒绝使用默认库"}' > "$alert"
    echo "CONFIG_MISSING"
    return 5
  fi
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
  if [[ -z "${APP_DB_PATH:-}" || ! -f "$APP_DB_PATH" ]]; then
    printf '%s\n' '{"kind":"config","reason":"config_db_missing","note":"APP_DB_PATH 未设置或不是已有文件，拒绝使用默认库"}' > "$alert"
    echo "CONFIG_DB_MISSING"
    return 5
  fi
  rm -f "$alert"
  return 0
}
