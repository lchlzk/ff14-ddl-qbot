import asyncio
from contextlib import suppress
from importlib import import_module
from importlib.util import find_spec
import json
import os
from pathlib import Path

import nonebot
from fastapi import FastAPI
from nonebot.log import logger

from bot_tools.security import initialize_nonebot_safely
from bot_tools.qq_transport import ReliableQQAdapter
from bot_tools.qq_transport import QQBotRuntime
from bot_tools.bot_credentials import configure_runtime
from bot_tools.web_admin import install_web_admin


def configure_simple_environment() -> list[dict[str, object]]:
    """Load encrypted admin-managed credentials into the QQ adapter config."""
    os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")
    os.environ.setdefault("COMMAND_START", '["/"]')
    os.environ.setdefault("QQ_IS_SANDBOX", "false")
    os.environ.setdefault("QQ_VERIFY_WEBHOOK", "true")
    return [bot.adapter_config() for bot in configure_runtime()]


qq_bot_configs = configure_simple_environment()


# Initialization resets NoneBot's logger; install redaction across that reset
# so private AI keys and one-time invitations cannot enter raw event logs.
initialize_nonebot_safely(qq_bots=qq_bot_configs)

driver = nonebot.get_driver()
driver.register_adapter(ReliableQQAdapter)
qq_adapter = nonebot.get_adapter(ReliableQQAdapter)


def load_third_party_plugins() -> None:
    """Load pip-installed plugins managed by plugin.ps1 or plugin.sh."""
    manifest_path = Path(__file__).with_name("third_party_plugins.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read plugin manifest: {manifest_path}") from exc

    if not isinstance(manifest, dict) or not isinstance(manifest.get("plugins"), list):
        raise RuntimeError("Plugin manifest must contain a 'plugins' array")

    modules: list[str] = []
    for index, plugin in enumerate(manifest["plugins"]):
        if not isinstance(plugin, dict):
            raise RuntimeError(f"Plugin manifest entry {index} must be an object")

        package_name = plugin.get("package")
        module_name = plugin.get("module")
        if not isinstance(package_name, str) or not package_name.strip():
            raise RuntimeError(f"Plugin manifest entry {index} has no package name")
        if not isinstance(module_name, str) or not module_name.strip():
            raise RuntimeError(f"Plugin manifest entry {index} has no module name")
        if not all(part.isidentifier() for part in module_name.split(".")):
            raise RuntimeError(f"Invalid plugin module name: {module_name}")
        if module_name in modules:
            raise RuntimeError(f"Duplicate plugin module name: {module_name}")
        modules.append(module_name)

    for module_name in modules:
        try:
            loaded_plugin = nonebot.load_plugin(module_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to load third-party plugin: {module_name}") from exc
        if loaded_plugin is None:
            raise RuntimeError(f"Failed to load third-party plugin: {module_name}")


FIRST_PARTY_PLUGINS = (
    "plugins.toolbox",
    "plugins.ai_chat",
    "plugins.bililive",
    "plugins.fflogs",
    "plugins.ffxiv_tools",
    "plugins.learning_chat",
    "plugins.otterbot",
    "plugins.ping",
    "plugins.trickcal",
)


def load_first_party_plugins() -> set[str]:
    """Load only installed first-party packages; each one is optional."""
    loaded_modules: set[str] = set()
    if find_spec("plugins") is None:
        return loaded_modules
    for module_name in FIRST_PARTY_PLUGINS:
        if find_spec(module_name) is None:
            continue
        if nonebot.load_plugin(module_name) is None:
            raise RuntimeError(f"Failed to load first-party plugin: {module_name}")
        loaded_modules.add(module_name)
    return loaded_modules


loaded_first_party_plugins = load_first_party_plugins()
load_third_party_plugins()
if "plugins.bililive" in loaded_first_party_plugins:
    import_module("plugins.bililive").configure_adapter(qq_adapter)

app: FastAPI = nonebot.get_app()
web_admin = install_web_admin(app)
qq_runtime = QQBotRuntime(qq_adapter, web_admin.credentials)
web_admin.bot_runtime = qq_runtime
# Installed plugins own their routes and lifecycle; core does not import their services.
for module_name in sorted(loaded_first_party_plugins):
    install = getattr(import_module(module_name), "install_web", None)
    if install is not None:
        install(app, web_admin.store)
maintenance_task = None


@driver.on_startup
async def start_maintenance():
    global maintenance_task
    from bot_tools.maintenance import worker
    maintenance_task = asyncio.create_task(worker(web_admin.store))


@driver.on_shutdown
async def stop_maintenance():
    if maintenance_task:
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
    from bot_tools.http_clients import close
    await close()


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Report process and ASGI liveness for the container health check."""
    return {"status": "ok"}


if __name__ == "__main__":
    nonebot.run()
