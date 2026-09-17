"""Optional plugin contributions to shared group settings and summaries."""
from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class GroupExtension:
    help_lines: tuple[str, ...]
    overview: Callable[[dict], list[str]]
    configure: Callable[[dict, list[str]], str | None]
    summary: Callable[[dict], dict]
    admin_fields: Callable[[dict], list[dict]] = lambda doc: []
    admin_details: Callable[[dict], list[dict]] = lambda doc: []
    admin_update: Callable[[dict, dict], None] = lambda doc, data: None
    search_keys: tuple[str, ...] = ()


_extensions: dict[str, GroupExtension] = {}


def register(name: str, extension: GroupExtension) -> None:
    _extensions[name] = extension


def help_lines() -> list[str]:
    return [line for extension in _extensions.values() for line in extension.help_lines]


def overview(doc: dict) -> list[str]:
    return [line for extension in _extensions.values() for line in extension.overview(doc)]


def configure(doc: dict, args: list[str]) -> str | None:
    for extension in _extensions.values():
        result = extension.configure(doc, args)
        if result is not None:
            return result
    return None


def summary(doc: dict) -> dict:
    result = {}
    for extension in _extensions.values():
        result.update(extension.summary(doc))
    return result


def admin_fields(doc: dict) -> list[dict]:
    return [row for extension in _extensions.values() for row in extension.admin_fields(doc)]


def admin_details(doc: dict) -> list[dict]:
    return [row for extension in _extensions.values() for row in extension.admin_details(doc)]


def admin_update(doc: dict, data: dict) -> None:
    for extension in _extensions.values():
        extension.admin_update(doc, data)


def search_keys() -> list[str]:
    return sorted({key for extension in _extensions.values() for key in extension.search_keys})
