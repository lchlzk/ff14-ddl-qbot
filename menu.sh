#!/usr/bin/env bash

set -Eeuo pipefail

show_help() {
    cat <<'EOF'
QQ menu and command-panel manager

Usage:
  ./menu.sh help
  ./menu.sh status [--bot-id BOT_ID]
  ./menu.sh sync [--force-menu] [--bot-id BOT_ID]

Edit qq-menu.json to add commands. The script creates or updates the managed
C2C/group/channel command panels. It refuses to replace a different existing C2C
custom menu unless --force-menu is supplied.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 2
}

action="help"
action_set=false
force_menu=false
bot_id=""

while (($# > 0)); do
    case "$1" in
        help|status|sync)
            if [[ "$action_set" == true ]]; then
                fail "Only one action may be specified."
            fi
            action="$1"
            action_set=true
            shift
            ;;
        -h|--help)
            if [[ "$action_set" == true ]]; then
                fail "Only one action may be specified."
            fi
            action="help"
            action_set=true
            shift
            ;;
        --force-menu)
            force_menu=true
            shift
            ;;
        --bot-id)
            (($# >= 2)) || fail "--bot-id requires a value."
            [[ -n "$2" ]] || fail "--bot-id requires a non-empty value."
            bot_id="$2"
            shift 2
            ;;
        --bot-id=*)
            bot_id="${1#--bot-id=}"
            [[ -n "$bot_id" ]] || fail "--bot-id requires a non-empty value."
            shift
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

if [[ "$action" == "help" ]]; then
    show_help
    exit 0
fi

if [[ "$action" == "status" && "$force_menu" == true ]]; then
    fail "--force-menu is only valid with the sync action."
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
compose_file="$script_dir/compose.yaml"
compose_command=()

command -v docker >/dev/null 2>&1 || fail "docker was not found. Start Docker or add it to PATH."
if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    fail "the Docker daemon is not running."
fi
if docker compose version >/dev/null 2>&1; then
    compose_command=(docker compose)
elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    compose_command=(docker-compose)
else
    fail "neither the Docker Compose plugin nor docker-compose is available."
fi

[[ -f "$compose_file" ]] || fail "Compose file not found: $compose_file"

compose_args=(
    --project-directory "$script_dir"
    --file "$compose_file"
    run --rm --no-deps
    qqbot python qq_menu.py "$action"
)

if [[ "$force_menu" == true ]]; then
    compose_args+=(--force-menu)
fi
if [[ -n "$bot_id" ]]; then
    compose_args+=(--bot-id "$bot_id")
fi

if "${compose_command[@]}" "${compose_args[@]}"; then
    exit 0
else
    exit_code=$?
    printf 'QQ menu command failed with exit code %d.\n' "$exit_code" >&2
    exit "$exit_code"
fi
