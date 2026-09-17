#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
compose_file="$project_dir/compose.yaml"
environment_file="$project_dir/.env"
environment_example="$project_dir/.env.example"
image_name="local/nonebot-qq:1.7.2"
image_archive="$project_dir/nonebot-qq.tar"
expected_image_id_file="$project_dir/image-id.txt"
compose_command=()

show_help() {
  cat <<'EOF'
NoneBot QQ one-click manager

Usage:
  ./bot.sh setup           Create a .env without QQ credentials
  ./bot.sh setup --force   Replace an existing .env
  ./bot.sh otter-setup     Securely add OtterBot FFXIV WebAPI credentials
  ./bot.sh fflogs-setup    Securely add FF Logs API v2 credentials
  ./bot.sh start           Start, or reload a changed .env
  ./bot.sh restart         Same safe recreate operation as start
  ./bot.sh build           Rebuild the image, then start it
  ./bot.sh status          Show container status
  ./bot.sh logs            Follow bot logs (Ctrl+C to stop following)
  ./bot.sh stop            Stop and remove the bot container
  ./bot.sh admin token     本机生成一次性授权码 / Create one-time private invitation
  ./bot.sh admin add CODE  确认私聊身份，绑定总管理员 / Confirm verified private identity
  ./bot.sh admin remove CODE 撤销权限 / Revoke identity
  ./bot.sh admin list      查看管理员 / List authorized identities
  ./bot.sh web account     设置或重置网页后台账号密码 / Set or reset web account
  ./bot.sh web status      查看网页会话数量 / Show active web sessions
  ./bot.sh web revoke-all  撤销全部网页会话 / Revoke every web session
EOF
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker was not found. Install Docker Engine or Docker Desktop first." >&2
    exit 1
  fi
  if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "Error: the Docker daemon is not running." >&2
    exit 1
  fi
  if docker compose version >/dev/null 2>&1; then
    compose_command=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    compose_command=(docker-compose)
  else
    echo "Error: neither the Docker Compose plugin nor docker-compose is available." >&2
    exit 1
  fi
}

compose() {
  "${compose_command[@]}" --project-directory "$project_dir" --file "$compose_file" "$@"
}

import_packaged_image_if_needed() {
  local current_image_id="" expected_image_id="" needs_import=false loaded_image_id=""
  if ! current_image_id="$(docker image ls --no-trunc --quiet "$image_name")"; then
    echo "Error: unable to query Docker images. Check that Docker is running." >&2
    exit 1
  fi
  if [[ ! -f "$image_archive" && ! -f "$expected_image_id_file" ]]; then
    if [[ -z "$current_image_id" && ! -f "$project_dir/Dockerfile" ]]; then
      echo "Error: nonebot-qq.tar is missing. Extract the complete offline package, then run ./bot.sh start again." >&2
      exit 1
    fi
    return
  fi
  if [[ -f "$expected_image_id_file" ]]; then
    IFS= read -r expected_image_id <"$expected_image_id_file" || true
    expected_image_id="${expected_image_id%$'\r'}"
    if [[ ! "$expected_image_id" =~ ^sha256:[a-fA-F0-9]{64}$ ]]; then
      echo "Error: image-id.txt is invalid. Extract the complete offline package again." >&2
      exit 1
    fi
  fi
  if [[ -z "$current_image_id" || (-n "$expected_image_id" && "$current_image_id" != "$expected_image_id") ]]; then
    needs_import=true
  fi
  if [[ "$needs_import" != true ]]; then
    return
  fi
  if [[ ! -f "$image_archive" ]]; then
    echo "Error: nonebot-qq.tar is missing and the installed image does not match this package. Extract the complete offline package again." >&2
    exit 1
  fi
  echo "Importing the packaged Docker image (first install or package update)..."
  docker load --input "$image_archive"
  loaded_image_id="$(docker image ls --no-trunc --quiet "$image_name")"
  if [[ -z "$loaded_image_id" || (-n "$expected_image_id" && "$loaded_image_id" != "$expected_image_id") ]]; then
    echo "Error: the imported image does not match this package. Extract the complete offline package again." >&2
    exit 1
  fi
}

setup_environment() {
  local overwrite="${1:-false}"
  if [[ -e "$environment_file" && "$overwrite" != "true" ]]; then
    echo ".env already exists; it was not changed. Use './bot.sh setup --force' to replace it."
    return
  fi

  local temporary_file
  temporary_file="$(mktemp "$project_dir/.env.XXXXXX")"
  chmod 600 "$temporary_file"
  cp -- "$environment_example" "$temporary_file"
  chmod 600 "$temporary_file"
  mv -f -- "$temporary_file" "$environment_file"
  echo ".env created without QQ credentials."
  echo "Start the service, run './bot.sh web account', then add one or more bots in http://127.0.0.1:8080/admin."
}

setup_otter_environment() {
  if [[ ! -f "$environment_file" ]]; then
    echo "No .env found; starting the QQ setup wizard first."
    setup_environment false
  fi

  local personal_qq otter_token temporary_file line trimmed_line
  read -r -p "Personal numeric QQ used for the Otter API token: " personal_qq
  read -r -s -p "Otter API Token (input is hidden): " otter_token
  printf '\n'
  if [[ ! "$personal_qq" =~ ^[0-9]{5,20}$ ]]; then
    echo "Error: the Otter API QQ must be your personal numeric QQ number." >&2
    exit 1
  fi
  if [[ ! "$otter_token" =~ ^[A-Za-z0-9._~-]{1,16}$ ]]; then
    echo "Error: use a 1-16 character token containing letters, digits, '.', '_', '~', or '-'. A random 12-16 character token is recommended." >&2
    exit 1
  fi

  temporary_file="$(mktemp "$project_dir/.env.XXXXXX")"
  chmod 600 "$temporary_file"
  trap 'rm -f -- "$temporary_file"' RETURN
  {
    while IFS= read -r line || [[ -n "$line" ]]; do
      trimmed_line="${line#"${line%%[![:space:]]*}"}"
      case "$trimmed_line" in
        OTTER_API_QQ=*|OTTER_API_TOKEN=*|OTTER_API_BASE=*|OTTER_INCLUDE_URLS=*) continue ;;
        *) printf '%s\n' "$line" ;;
      esac
    done <"$environment_file"
    printf '\n%s\n' '# Optional FFXIV queries powered by OtterBot WebAPI.'
    printf 'OTTER_API_QQ=%s\n' "$personal_qq"
    printf 'OTTER_API_TOKEN=%s\n' "$otter_token"
    printf '%s\n' 'OTTER_API_BASE=https://xn--v9x.net/api/'
    printf '%s\n' 'OTTER_INCLUDE_URLS=false'
  } >"$temporary_file"
  mv -f -- "$temporary_file" "$environment_file"
  trap - RETURN
  chmod 600 "$environment_file"
  unset otter_token
  echo "OtterBot WebAPI settings saved. Run './bot.sh start' to load them."
}

setup_fflogs_environment() {
  if [[ ! -f "$environment_file" ]]; then
    echo "No .env found; starting the QQ setup wizard first."
    setup_environment false
  fi

  local client_id client_secret default_region default_zone temporary_file line trimmed_line
  read -r -p "FF Logs Client ID: " client_id
  read -r -s -p "FF Logs Client Secret (input is hidden): " client_secret
  printf '\n'
  read -r -p "Default FF Logs region [CN]: " default_region
  default_region="${default_region:-CN}"
  default_region="${default_region^^}"
  read -r -p "Default FF Logs zone ID [automatic]: " default_zone

  if [[ ! "$client_id" =~ ^[A-Za-z0-9._~-]{3,256}$ ]]; then
    echo "Error: FF Logs Client ID is empty or contains unsupported characters." >&2
    exit 1
  fi
  if [[ ! "$client_secret" =~ ^[A-Za-z0-9._~-]{3,512}$ ]]; then
    echo "Error: FF Logs Client Secret is empty or contains unsupported characters." >&2
    exit 1
  fi
  if [[ ! "$default_region" =~ ^(CN|JP|NA|EU|KR|OC)$ ]]; then
    echo "Error: region must be a short code such as CN, JP, NA, EU, KR, or OC." >&2
    exit 1
  fi
  if [[ -n "$default_zone" ]] && { [[ ! "$default_zone" =~ ^[0-9]+$ ]] || ((10#$default_zone < 1 || 10#$default_zone > 100000)); }; then
    echo "Error: zone ID must be blank or an integer from 1 to 100000." >&2
    exit 1
  fi

  temporary_file="$(mktemp "$project_dir/.env.XXXXXX")"
  chmod 600 "$temporary_file"
  trap 'rm -f -- "$temporary_file"' RETURN
  {
    while IFS= read -r line || [[ -n "$line" ]]; do
      trimmed_line="${line#"${line%%[![:space:]]*}"}"
      case "$trimmed_line" in
        FFLOGS_CLIENT_ID=*|FFLOGS_CLIENT_SECRET=*|FFLOGS_DEFAULT_REGION=*|FFLOGS_DEFAULT_ZONE_ID=*|FFLOGS_TIMEOUT=*|FFLOGS_CACHE_TTL=*|FFLOGS_GLOBAL_INTERVAL=*) continue ;;
        *) printf '%s\n' "$line" ;;
      esac
    done <"$environment_file"
    printf '\n%s\n' '# Optional modern DPS and raid queries powered by the official FF Logs API v2.'
    printf 'FFLOGS_CLIENT_ID=%s\n' "$client_id"
    printf 'FFLOGS_CLIENT_SECRET=%s\n' "$client_secret"
    printf 'FFLOGS_DEFAULT_REGION=%s\n' "$default_region"
    printf 'FFLOGS_DEFAULT_ZONE_ID=%s\n' "$default_zone"
    printf '%s\n' 'FFLOGS_TIMEOUT=18'
    printf '%s\n' 'FFLOGS_CACHE_TTL=600'
    printf '%s\n' 'FFLOGS_GLOBAL_INTERVAL=0.5'
  } >"$temporary_file"
  mv -f -- "$temporary_file" "$environment_file"
  trap - RETURN
  chmod 600 "$environment_file"
  unset client_secret
  echo "FF Logs API settings saved. Run './bot.sh start' to load them."
}

wait_healthy() {
  local attempt container_id state runtime_status health_status
  for ((attempt = 0; attempt < 60; attempt++)); do
    container_id="$(compose ps --all --quiet qqbot)"
    if [[ -n "$container_id" ]]; then
      state="$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
      runtime_status="${state%%|*}"
      health_status="${state#*|}"
      if [[ "$runtime_status" == "running" && ("$health_status" == "healthy" || "$health_status" == "none") ]]; then
        echo "QQ bot is running and healthy."
        return
      fi
      if [[ "$runtime_status" =~ ^(restarting|exited|dead)$ || "$health_status" == "unhealthy" ]]; then
        break
      fi
    fi
    sleep 2
  done

  compose logs --no-color --tail 100 qqbot || true
  echo "Error: QQ bot did not become healthy. The last 100 log lines are shown above." >&2
  exit 1
}

action="${1:-help}"
option="${2:-}"
case "$action" in
  help|-h|--help)
    show_help
    exit 0
    ;;
  setup)
    if [[ -n "$option" && "$option" != "--force" ]]; then
      echo "Error: unknown setup option '$option'." >&2
      exit 2
    fi
    setup_environment "$([[ "$option" == "--force" ]] && echo true || echo false)"
    exit 0
    ;;
  otter-setup)
    if [[ -n "$option" ]]; then
      echo "Error: otter-setup does not accept additional options." >&2
      exit 2
    fi
    setup_otter_environment
    exit 0
    ;;
  fflogs-setup)
    if [[ -n "$option" ]]; then
      echo "Error: fflogs-setup does not accept additional options." >&2
      exit 2
    fi
    setup_fflogs_environment
    exit 0
    ;;
  start|restart|build|stop|status|logs|admin|web)
    ;;
  *)
    echo "Error: unknown action '$action'." >&2
    show_help >&2
    exit 2
    ;;
esac

if [[ ! -f "$environment_file" ]]; then
  echo "No .env found; starting the setup wizard."
  setup_environment false
fi

require_docker
case "$action" in
  admin)
    admin_action="${2:-list}"
    case "$admin_action" in
      token|list)
        [[ $# -le 2 ]] || { echo "This action takes no identity code." >&2; exit 2; }
        compose exec -T qqbot python -m bot_tools.admin "$admin_action"
        ;;
      add|remove)
        identity_code="${3:-}"
        [[ "$identity_code" =~ ^[a-fA-F0-9]{16}$ ]] || { echo "Use the identity code: admin token -> private /bot whoami TOKEN -> admin add CODE / 请填写私聊兑换的身份码" >&2; exit 2; }
        compose exec -T qqbot python -m bot_tools.admin "$admin_action" "$identity_code"
        ;;
      *) echo "Use admin token/add/remove/list" >&2; exit 2 ;;
    esac
    ;;
  web)
    web_action="${2:-status}"
    [[ $# -le 2 ]] || { echo "Web administration actions do not accept extra arguments." >&2; exit 2; }
    case "$web_action" in
      account)
        read -r -p "Web admin username / 网页后台用户名: " web_username
        read -r -s -p "Web admin password (10-128 chars) / 网页后台密码（10-128 位）: " web_password
        printf '\n'
        read -r -s -p "Confirm password / 再输入一次密码: " web_confirmation
        printf '\n'
        [[ "$web_password" == "$web_confirmation" ]] || { echo "The two passwords do not match / 两次输入的密码不一致。" >&2; exit 2; }
        printf '%s\n%s\n' "$web_username" "$web_password" | compose exec -T qqbot python -m bot_tools.web_admin account
        unset web_password web_confirmation
        ;;
      status|revoke-all)
        compose exec -T qqbot python -m bot_tools.web_admin "$web_action"
        ;;
      *) echo "Use web account/status/revoke-all" >&2; exit 2 ;;
    esac
    ;;
  start|restart)
    import_packaged_image_if_needed
    compose up --detach --force-recreate qqbot
    wait_healthy
    ;;
  build)
    compose build qqbot
    compose up --detach --force-recreate qqbot
    wait_healthy
    ;;
  stop)
    compose down
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs --follow --tail 100 qqbot
    ;;
esac
