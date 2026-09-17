#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
MANIFEST_FILE="${SCRIPT_DIR}/third_party_plugins.json"
PLUGIN_REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements-plugins.txt"
HELPER_FILE="${SCRIPT_DIR}/plugin_manifest.py"
HELPER_IMAGE="${PLUGIN_HELPER_IMAGE:-local/nonebot-qq:1.7.2}"
HEALTH_TIMEOUT_SECONDS="${PLUGIN_HEALTH_TIMEOUT_SECONDS:-120}"

DOCKER_BIN=""
WORK_DIR=""
TEMP_ROOT=""
ORIGINAL_DIR=""
STAGE_DIR=""
ORIGINAL_MANIFEST_EXISTS=0
ORIGINAL_REQUIREMENTS_EXISTS=0
CONFIG_COMMITTED=0
RESTORE_CONFIG_ON_EXIT=0

OLD_CONTAINER_EXISTS=0
OLD_CONTAINER_RUNNING=0
OLD_CONTAINER_ID=""
OLD_IMAGE_ID=""
OLD_IMAGE_REF=""
BUILD_COMPLETED=0
RESTART_ATTEMPTED=0

COMPOSE_CMD=()

usage() {
    printf '%s\n' \
        'NoneBot Docker plugin manager' \
        '' \
        'Usage:' \
        '  ./plugin.sh add <pip-package> [python-module] [--no-build]' \
        '  ./plugin.sh remove <pip-package-or-module> [--no-build]' \
        '  ./plugin.sh list' \
        '  ./plugin.sh rebuild' \
        '' \
        'Examples:' \
        '  ./plugin.sh add nonebot-plugin-apscheduler' \
        "  ./plugin.sh add 'some-package==1.2.3' some_python_module" \
        '  ./plugin.sh remove nonebot-plugin-apscheduler' \
        '' \
        'The Python module defaults to the package name with hyphens and dots' \
        'replaced by underscores. Use --no-build to update only the two managed' \
        'plugin files; the running container is not changed.'
}

die() {
    printf 'plugin.sh: %s\n' "$*" >&2
    exit 2
}

warn() {
    printf 'plugin.sh warning: %s\n' "$*" >&2
}

resolve_docker() {
    local candidate=""
    if candidate="$(command -v docker 2>/dev/null)" && [[ -n "${candidate}" ]]; then
        DOCKER_BIN="${candidate}"
    elif [[ -x /usr/local/bin/docker ]]; then
        DOCKER_BIN=/usr/local/bin/docker
    elif [[ -x /usr/bin/docker ]]; then
        DOCKER_BIN=/usr/bin/docker
    elif [[ -x /snap/bin/docker ]]; then
        DOCKER_BIN=/snap/bin/docker
    else
        die 'Docker CLI was not found. Install Docker and ensure docker is on PATH.'
    fi
    COMPOSE_CMD=(
        "${DOCKER_BIN}" compose
        --project-directory "${SCRIPT_DIR}"
        --file "${COMPOSE_FILE}"
    )
}

ensure_docker() {
    resolve_docker
    if ! "${DOCKER_BIN}" info >/dev/null; then
        die 'Docker is unavailable. Start the Docker daemon and try again.'
    fi
}

ensure_compose() {
    [[ -f "${COMPOSE_FILE}" ]] || die "Compose file not found: ${COMPOSE_FILE}"
    if ! "${DOCKER_BIN}" compose version >/dev/null; then
        die 'The Docker Compose plugin is unavailable. Install Docker Compose v2.'
    fi
    if [[ ! "${HEALTH_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
        die 'PLUGIN_HEALTH_TIMEOUT_SECONDS must be a positive integer.'
    fi
}

prepare_work_directory() {
    [[ -f "${HELPER_FILE}" ]] || die "Manifest helper not found: ${HELPER_FILE}"
    TEMP_ROOT="$(CDPATH= cd -- "${TMPDIR:-/tmp}" && pwd -P)"
    WORK_DIR="$(mktemp -d "${TEMP_ROOT}/qqbot-plugin.XXXXXXXX")"
    ORIGINAL_DIR="${WORK_DIR}/original"
    STAGE_DIR="${WORK_DIR}/stage"
    mkdir -- "${ORIGINAL_DIR}" "${STAGE_DIR}"
    cp -p -- "${HELPER_FILE}" "${STAGE_DIR}/plugin_manifest.py"

    if [[ -e "${MANIFEST_FILE}" && ! -f "${MANIFEST_FILE}" ]]; then
        die "Plugin manifest is not a regular file: ${MANIFEST_FILE}"
    fi
    if [[ -f "${MANIFEST_FILE}" ]]; then
        ORIGINAL_MANIFEST_EXISTS=1
        cp -p -- "${MANIFEST_FILE}" "${ORIGINAL_DIR}/third_party_plugins.json"
        cp -p -- "${MANIFEST_FILE}" "${STAGE_DIR}/third_party_plugins.json"
    fi

    if [[ -e "${PLUGIN_REQUIREMENTS_FILE}" && ! -f "${PLUGIN_REQUIREMENTS_FILE}" ]]; then
        die "Plugin requirements path is not a regular file: ${PLUGIN_REQUIREMENTS_FILE}"
    fi
    if [[ -f "${PLUGIN_REQUIREMENTS_FILE}" ]]; then
        ORIGINAL_REQUIREMENTS_EXISTS=1
        cp -p -- "${PLUGIN_REQUIREMENTS_FILE}" "${ORIGINAL_DIR}/requirements-plugins.txt"
        cp -p -- "${PLUGIN_REQUIREMENTS_FILE}" "${STAGE_DIR}/requirements-plugins.txt"
    fi
}

run_manifest_helper() {
    local mount_options='rw'
    local selinux_state=''
    if command -v getenforce >/dev/null 2>&1; then
        selinux_state="$(getenforce 2>/dev/null || true)"
        if [[ "${selinux_state}" == 'Enforcing' || "${selinux_state}" == 'Permissive' ]]; then
            mount_options='rw,Z'
        fi
    fi

    "${DOCKER_BIN}" run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --user "$(id -u):$(id -g)" \
        --env PYTHONDONTWRITEBYTECODE=1 \
        --volume "${STAGE_DIR}:/work:${mount_options}" \
        --workdir /work \
        "${HELPER_IMAGE}" \
        python /work/plugin_manifest.py /work "$@"
}

atomic_copy() {
    local source_file="$1"
    local target_file="$2"
    local temporary_file=""
    temporary_file="$(mktemp "${target_file}.tmp.XXXXXXXX")"
    if ! cp -p -- "${source_file}" "${temporary_file}"; then
        rm -f -- "${temporary_file}"
        return 1
    fi
    if ! mv -f -- "${temporary_file}" "${target_file}"; then
        rm -f -- "${temporary_file}"
        return 1
    fi
}

restore_configuration() {
    local failed=0
    if (( ORIGINAL_MANIFEST_EXISTS )); then
        if ! atomic_copy \
            "${ORIGINAL_DIR}/third_party_plugins.json" "${MANIFEST_FILE}"; then
            warn 'Could not restore third_party_plugins.json.'
            failed=1
        fi
    elif ! rm -f -- "${MANIFEST_FILE}"; then
        warn 'Could not remove the newly created third_party_plugins.json.'
        failed=1
    fi

    if (( ORIGINAL_REQUIREMENTS_EXISTS )); then
        if ! atomic_copy \
            "${ORIGINAL_DIR}/requirements-plugins.txt" \
            "${PLUGIN_REQUIREMENTS_FILE}"; then
            warn 'Could not restore requirements-plugins.txt.'
            failed=1
        fi
    elif ! rm -f -- "${PLUGIN_REQUIREMENTS_FILE}"; then
        warn 'Could not remove the newly created requirements-plugins.txt.'
        failed=1
    fi

    if (( failed == 0 )); then
        CONFIG_COMMITTED=0
        RESTORE_CONFIG_ON_EXIT=0
    fi
    return "${failed}"
}

commit_staged_configuration() {
    [[ -f "${STAGE_DIR}/third_party_plugins.json" ]] || \
        die 'The manifest helper did not produce third_party_plugins.json.'
    [[ -f "${STAGE_DIR}/requirements-plugins.txt" ]] || \
        die 'The manifest helper did not produce requirements-plugins.txt.'

    CONFIG_COMMITTED=1
    RESTORE_CONFIG_ON_EXIT=1
    if ! atomic_copy \
        "${STAGE_DIR}/third_party_plugins.json" "${MANIFEST_FILE}"; then
        warn 'Could not write third_party_plugins.json.'
        restore_configuration || true
        return 1
    fi
    if ! atomic_copy \
        "${STAGE_DIR}/requirements-plugins.txt" \
        "${PLUGIN_REQUIREMENTS_FILE}"; then
        warn 'Could not write requirements-plugins.txt; restoring both files.'
        restore_configuration || true
        return 1
    fi
    return 0
}

first_line() {
    local value="$1"
    printf '%s' "${value%%$'\n'*}"
}

capture_deployment_state() {
    local ids=""
    local image_refs=""
    OLD_CONTAINER_EXISTS=0
    OLD_CONTAINER_RUNNING=0
    OLD_CONTAINER_ID=""
    OLD_IMAGE_ID=""
    OLD_IMAGE_REF=""

    if ids="$("${COMPOSE_CMD[@]}" ps --all --quiet qqbot 2>/dev/null)"; then
        OLD_CONTAINER_ID="$(first_line "${ids}")"
    fi
    if [[ -n "${OLD_CONTAINER_ID}" ]]; then
        OLD_CONTAINER_EXISTS=1
        OLD_IMAGE_ID="$(
            "${DOCKER_BIN}" inspect --format '{{.Image}}' \
                "${OLD_CONTAINER_ID}" 2>/dev/null || true
        )"
        OLD_IMAGE_REF="$(
            "${DOCKER_BIN}" inspect --format '{{.Config.Image}}' \
                "${OLD_CONTAINER_ID}" 2>/dev/null || true
        )"
        if [[ "$(
            "${DOCKER_BIN}" inspect --format '{{.State.Running}}' \
                "${OLD_CONTAINER_ID}" 2>/dev/null || true
        )" == 'true' ]]; then
            OLD_CONTAINER_RUNNING=1
        fi
        return
    fi

    if image_refs="$("${COMPOSE_CMD[@]}" config --images 2>/dev/null)"; then
        OLD_IMAGE_REF="$(first_line "${image_refs}")"
    fi
    if [[ -n "${OLD_IMAGE_REF}" ]]; then
        OLD_IMAGE_ID="$(
            "${DOCKER_BIN}" image inspect --format '{{.Id}}' \
                "${OLD_IMAGE_REF}" 2>/dev/null || true
        )"
    fi
    return 0
}

show_failure_logs() {
    warn 'Container startup failed. Review logs manually with:'
    warn "${DOCKER_BIN} compose --project-directory '${SCRIPT_DIR}' --file '${COMPOSE_FILE}' logs --tail 100 qqbot"
}

wait_bot_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
    local ids=""
    local container_id=""
    local state=""
    local health=""

    while (( SECONDS < deadline )); do
        if ids="$("${COMPOSE_CMD[@]}" ps --all --quiet qqbot 2>/dev/null)"; then
            container_id="$(first_line "${ids}")"
        else
            container_id=""
        fi
        if [[ -n "${container_id}" ]]; then
            state="$(
                "${DOCKER_BIN}" inspect --format '{{.State.Status}}' \
                    "${container_id}" 2>/dev/null || true
            )"
            health="$(
                "${DOCKER_BIN}" inspect \
                    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
                    "${container_id}" 2>/dev/null || true
            )"
            case "${state}:${health}" in
                running:healthy)
                    printf '%s\n' 'QQ bot is healthy.'
                    return 0
                    ;;
                running:none)
                    printf '%s\n' 'QQ bot is running (no container healthcheck is defined).'
                    return 0
                    ;;
                exited:*|dead:*|*:unhealthy)
                    show_failure_logs
                    return 1
                    ;;
            esac
        fi
        sleep 2
    done

    show_failure_logs
    warn "QQ bot did not become healthy within ${HEALTH_TIMEOUT_SECONDS} seconds."
    return 1
}

restore_previous_deployment() {
    local restored_image=0
    local failed=0

    if [[ -n "${OLD_IMAGE_ID}" && -n "${OLD_IMAGE_REF}" ]]; then
        if "${DOCKER_BIN}" image tag "${OLD_IMAGE_ID}" "${OLD_IMAGE_REF}"; then
            restored_image=1
        else
            warn 'Could not restore the previous Docker image tag.'
        fi
    fi

    if (( restored_image == 0 )); then
        warn 'The previous image could not be retagged; rebuilding restored files.'
        if "${COMPOSE_CMD[@]}" build qqbot; then
            restored_image=1
        else
            warn 'Could not rebuild the previous Docker image.'
            failed=1
        fi
    fi

    if (( RESTART_ATTEMPTED == 0 )); then
        return "${failed}"
    fi

    if (( OLD_CONTAINER_EXISTS == 0 )); then
        if ! "${COMPOSE_CMD[@]}" rm --stop --force qqbot; then
            warn 'Could not remove the container created by the failed deployment.'
            failed=1
        fi
    elif (( restored_image == 1 && OLD_CONTAINER_RUNNING == 1 )); then
        if "${COMPOSE_CMD[@]}" up --detach --force-recreate qqbot; then
            if ! wait_bot_healthy; then
                warn 'The previous container was recreated but is not healthy.'
                failed=1
            fi
        else
            warn 'Could not recreate the previous running container.'
            failed=1
        fi
    elif (( restored_image == 1 )); then
        if ! "${COMPOSE_CMD[@]}" create --force-recreate qqbot; then
            warn 'Could not recreate the previous stopped container.'
            failed=1
        fi
    fi

    return "${failed}"
}

handle_deployment_failure() {
    local restore_files="$1"
    local failed=0
    if (( restore_files == 1 && CONFIG_COMMITTED == 1 )); then
        warn 'Deployment failed; restoring the previous plugin files.'
        if ! restore_configuration; then
            failed=1
        fi
    fi
    if (( BUILD_COMPLETED == 1 )); then
        if ! restore_previous_deployment; then
            failed=1
        fi
    fi
    if (( failed == 0 )); then
        warn 'The previous plugin configuration and deployment were restored.'
    else
        warn 'Automatic rollback was incomplete. Inspect Docker before retrying.'
    fi
}

deploy_current_configuration() {
    local restore_files="$1"
    local command_status=1
    BUILD_COMPLETED=0
    RESTART_ATTEMPTED=0

    if "${COMPOSE_CMD[@]}" build qqbot; then
        BUILD_COMPLETED=1
    else
        command_status=$?
        if (( restore_files == 1 && CONFIG_COMMITTED == 1 )); then
            warn 'Docker build failed; restoring the previous plugin files.'
            restore_configuration || \
                warn 'Plugin file restoration was incomplete.'
        fi
        return "${command_status}"
    fi

    RESTART_ATTEMPTED=1
    if "${COMPOSE_CMD[@]}" up --detach --force-recreate qqbot; then
        :
    else
        command_status=$?
        handle_deployment_failure "${restore_files}"
        return "${command_status}"
    fi
    if wait_bot_healthy; then
        :
    else
        command_status=$?
        handle_deployment_failure "${restore_files}"
        return "${command_status}"
    fi

    RESTORE_CONFIG_ON_EXIT=0
    CONFIG_COMMITTED=0
    return 0
}

cleanup() {
    local original_status="$1"
    trap - EXIT
    set +e
    if (( original_status != 0 && RESTORE_CONFIG_ON_EXIT == 1 && CONFIG_COMMITTED == 1 )); then
        warn 'Unexpected interruption; restoring the previous plugin files.'
        restore_configuration
    fi
    if [[ -n "${WORK_DIR}" && -n "${TEMP_ROOT}" ]]; then
        case "${WORK_DIR}" in
            "${TEMP_ROOT}"/qqbot-plugin.*)
                rm -rf -- "${WORK_DIR}"
                ;;
            *)
                warn "Refusing to remove unexpected temporary path: ${WORK_DIR}"
                ;;
        esac
    fi
    exit "${original_status}"
}

trap 'cleanup $?' EXIT

ACTION="${1:-help}"
if (( $# > 0 )); then
    shift
fi
NO_BUILD=0
POSITIONAL=()
while (( $# > 0 )); do
    case "$1" in
        --no-build)
            NO_BUILD=1
            ;;
        --)
            shift
            while (( $# > 0 )); do
                POSITIONAL+=("$1")
                shift
            done
            break
            ;;
        -*)
            die "unknown option: $1"
            ;;
        *)
            POSITIONAL+=("$1")
            ;;
    esac
    shift
done

case "${ACTION}" in
    help|-h|--help)
        (( ${#POSITIONAL[@]} == 0 )) || die 'help does not accept arguments.'
        (( NO_BUILD == 0 )) || die '--no-build is valid only for add and remove.'
        usage
        ;;
    list)
        (( ${#POSITIONAL[@]} == 0 )) || die 'list does not accept arguments.'
        (( NO_BUILD == 0 )) || die '--no-build is valid only for add and remove.'
        ensure_docker
        prepare_work_directory
        run_manifest_helper list
        ;;
    add)
        (( ${#POSITIONAL[@]} >= 1 && ${#POSITIONAL[@]} <= 2 )) || \
            die 'add requires <pip-package> and an optional [python-module].'
        ensure_docker
        prepare_work_directory
        run_manifest_helper add "${POSITIONAL[@]}"
        if (( NO_BUILD == 0 )); then
            ensure_compose
            capture_deployment_state
        fi
        commit_staged_configuration
        if (( NO_BUILD == 1 )); then
            RESTORE_CONFIG_ON_EXIT=0
            CONFIG_COMMITTED=0
            printf '%s\n' \
                'Plugin files updated. Run ./plugin.sh rebuild when ready.'
        elif deploy_current_configuration 1; then
            printf '%s\n' 'Plugin installed and loaded.'
        else
            exit_status=$?
            exit "${exit_status}"
        fi
        ;;
    remove)
        (( ${#POSITIONAL[@]} == 1 )) || \
            die 'remove requires <pip-package-or-module>.'
        ensure_docker
        prepare_work_directory
        run_manifest_helper remove "${POSITIONAL[0]}"
        if (( NO_BUILD == 0 )); then
            ensure_compose
            capture_deployment_state
        fi
        commit_staged_configuration
        if (( NO_BUILD == 1 )); then
            RESTORE_CONFIG_ON_EXIT=0
            CONFIG_COMMITTED=0
            printf '%s\n' \
                'Plugin files updated. Run ./plugin.sh rebuild when ready.'
        elif deploy_current_configuration 1; then
            printf '%s\n' 'Plugin removed and container updated.'
        else
            exit_status=$?
            exit "${exit_status}"
        fi
        ;;
    rebuild)
        (( ${#POSITIONAL[@]} == 0 )) || die 'rebuild does not accept arguments.'
        (( NO_BUILD == 0 )) || die '--no-build is valid only for add and remove.'
        ensure_docker
        ensure_compose
        capture_deployment_state
        if deploy_current_configuration 0; then
            printf '%s\n' 'QQ bot image rebuilt and container updated.'
        else
            exit_status=$?
            exit "${exit_status}"
        fi
        ;;
    *)
        die "unknown action: ${ACTION}"
        ;;
esac
