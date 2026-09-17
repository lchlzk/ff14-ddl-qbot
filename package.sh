#!/usr/bin/env bash

set -Eeuo pipefail

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if (($# != 0)); then
    fail "Usage: ./package.sh"
fi

project_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
release_dir="$project_dir/release"
compose_file="$project_dir/compose.yaml"
dockerfile="$project_dir/Dockerfile"
dockerignore_file="$project_dir/.dockerignore"
image_name="local/nonebot-qq:1.7.2"

assert_release_target() {
    local expected parent base resolved
    expected="$project_dir/release"
    parent="$(dirname -- "$release_dir")"
    base="$(basename -- "$release_dir")"

    if [[ "$release_dir" != "$expected" || "$parent" != "$project_dir" || "$base" != "release" ]]; then
        fail "Refusing to refresh anything except the project's exact release directory."
    fi

    if [[ -L "$release_dir" ]]; then
        fail "Refusing to replace a release directory that is a symlink."
    fi
    if [[ -e "$release_dir" && ! -d "$release_dir" ]]; then
        fail "Release target exists but is not a directory: $release_dir"
    fi
    if [[ -d "$release_dir" ]]; then
        resolved="$(CDPATH= cd -- "$release_dir" && pwd -P)"
        [[ "$resolved" == "$expected" ]] ||
            fail "Resolved release directory is not the expected project release directory."
    fi
}

assert_staging_target() {
    local target="$1" parent base resolved
    parent="$(dirname -- "$target")"
    base="$(basename -- "$target")"

    if [[ "$parent" != "$project_dir" || "$base" != .release-staging.* ]]; then
        fail "Refusing to use a staging directory outside the project."
    fi
    [[ ! -L "$target" ]] ||
        fail "Release staging target must not be a symlink."
    if [[ -e "$target" && ! -d "$target" ]]; then
        fail "Release staging target must be a normal directory inside the project."
    fi
    if [[ -d "$target" ]]; then
        resolved="$(CDPATH= cd -- "$target" && pwd -P)"
        [[ "$resolved" == "$target" ]] ||
            fail "Resolved release staging directory does not match its expected path."
    fi
}

assert_source_file() {
    local source_path="$1"
    [[ -f "$source_path" ]] || fail "Required source is missing: $source_path"
    [[ ! -L "$source_path" ]] || fail "Required source must not be a symlink: $source_path"
}

release_sources=(
    ".env.example"
    "bot.cmd"
    "bot.ps1"
    "bot.sh"
    "menu.cmd"
    "menu.ps1"
    "menu.sh"
    "qq-menu.json"
    "compose.runtime.yaml"
    "START.txt"
    "TOOLBOX.md"
    "AI.md"
    "LEARNING_CHAT.md"
    "WEB_ADMIN.md"
    "TRICKCAL.md"
    "OPTIMIZATIONS.md"
    "LICENSES/AGPL-3.0-only.txt"
)

assert_release_target
for source_name in "${release_sources[@]}"; do
    assert_source_file "$project_dir/$source_name"
done
for build_source in "$compose_file" "$dockerfile" "$dockerignore_file"; do
    assert_source_file "$build_source"
done

grep -Fxq '.env' "$dockerignore_file" ||
    fail ".dockerignore must exclude .env before packaging."
grep -Fxq 'release/' "$dockerignore_file" ||
    fail ".dockerignore must exclude release/ before packaging."
grep -Fq 'YOUR_APP_ID' "$project_dir/.env.example" ||
    fail ".env.example must contain the AppID placeholder before packaging."
grep -Fq 'YOUR_APP_SECRET' "$project_dir/.env.example" ||
    fail ".env.example must contain the AppSecret placeholder before packaging."

command -v docker >/dev/null 2>&1 ||
    fail "docker was not found. Install Docker Engine or Docker Desktop first."
docker version --format '{{.Server.Version}}' >/dev/null ||
    fail "The Docker daemon is not running."
docker compose version >/dev/null ||
    fail "The Docker Compose plugin is not available."

printf 'Building %s ...\n' "$image_name"
docker build \
    --file "$dockerfile" \
    --tag "$image_name" \
    "$project_dir"

staging_dir="$(mktemp -d "$project_dir/.release-staging.XXXXXXXX")"
assert_staging_target "$staging_dir"
cleanup_staging() {
    if [[ -n "${staging_dir:-}" && -e "$staging_dir" ]]; then
        assert_staging_target "$staging_dir"
        rm -rf -- "$staging_dir"
    fi
}
trap cleanup_staging EXIT

cp -- "$project_dir/.env.example" "$staging_dir/.env.example"
cp -- "$project_dir/bot.cmd" "$staging_dir/bot.cmd"
cp -- "$project_dir/bot.ps1" "$staging_dir/bot.ps1"
cp -- "$project_dir/bot.sh" "$staging_dir/bot.sh"
cp -- "$project_dir/menu.cmd" "$staging_dir/menu.cmd"
cp -- "$project_dir/menu.ps1" "$staging_dir/menu.ps1"
cp -- "$project_dir/menu.sh" "$staging_dir/menu.sh"
cp -- "$project_dir/qq-menu.json" "$staging_dir/qq-menu.json"
cp -- "$project_dir/compose.runtime.yaml" "$staging_dir/compose.yaml"
cp -- "$project_dir/START.txt" "$staging_dir/START.txt"
cp -- "$project_dir/TOOLBOX.md" "$staging_dir/TOOLBOX.md"
cp -- "$project_dir/AI.md" "$staging_dir/AI.md"
cp -- "$project_dir/LEARNING_CHAT.md" "$staging_dir/LEARNING_CHAT.md"
cp -- "$project_dir/WEB_ADMIN.md" "$staging_dir/WEB_ADMIN.md"
cp -- "$project_dir/TRICKCAL.md" "$staging_dir/TRICKCAL.md"
cp -- "$project_dir/OPTIMIZATIONS.md" "$staging_dir/OPTIMIZATIONS.md"
cp -- "$project_dir/LICENSES/AGPL-3.0-only.txt" "$staging_dir/AGPL-3.0-only.txt"
chmod 755 "$staging_dir/bot.sh" "$staging_dir/menu.sh"
chmod 644 \
    "$staging_dir/.env.example" \
    "$staging_dir/bot.cmd" \
    "$staging_dir/bot.ps1" \
    "$staging_dir/menu.cmd" \
    "$staging_dir/menu.ps1" \
    "$staging_dir/qq-menu.json" \
    "$staging_dir/compose.yaml" \
    "$staging_dir/START.txt" \
    "$staging_dir/TOOLBOX.md" \
    "$staging_dir/AI.md" \
    "$staging_dir/LEARNING_CHAT.md" \
    "$staging_dir/WEB_ADMIN.md" \
    "$staging_dir/TRICKCAL.md" \
    "$staging_dir/OPTIMIZATIONS.md" \
    "$staging_dir/AGPL-3.0-only.txt"

printf 'Saving %s for offline use ...\n' "$image_name"
docker save --output "$staging_dir/nonebot-qq.tar" "$image_name"
docker image inspect --format '{{.Id}}' "$image_name" >"$staging_dir/image-id.txt"
[[ -s "$staging_dir/image-id.txt" ]] ||
    fail "Docker returned an empty image ID."
chmod 644 "$staging_dir/image-id.txt"

[[ -s "$staging_dir/nonebot-qq.tar" ]] ||
    fail "Docker produced an empty image archive."
[[ ! -e "$staging_dir/.env" ]] ||
    fail "Safety check failed: release must never contain .env."

docker run --rm --network none --read-only --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --volume "$staging_dir:/package" --volume "$project_dir/tools:/build:ro" \
    --entrypoint python "$image_name" /build/pack_release.py /package

assert_release_target
if [[ -d "$release_dir" ]]; then
    rm -rf -- "$release_dir"
fi
mv -- "$staging_dir" "$release_dir"
mv -f -- "$release_dir/QQbot-one-click.zip" "$project_dir/QQbot-one-click.zip"
staging_dir=""
trap - EXIT

archive_size="$(wc -c <"$release_dir/nonebot-qq.tar")"
printf 'Offline package created at %s (%s image bytes).\n' "$release_dir" "$archive_size"
printf 'Distribute: %s/QQbot-one-click.zip\n' "$project_dir"
