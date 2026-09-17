from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_BASE = "https://api.bot.qq.com"
TOKEN_URL = f"{API_BASE}/app/getAppAccessToken"
CONFIG_PATH = Path(__file__).with_name("qq-menu.json")
MANAGED_REMARK_PREFIX = "nonebot-qq-menu:"


class MenuManagerError(RuntimeError):
    """A safe-to-display configuration or QQ API error."""


@dataclass(frozen=True)
class Credentials:
    app_id: str
    client_secret: str


def weighted_length(value: str) -> int:
    """Count ASCII as 1 and non-ASCII as 2, matching QQ menu limits."""
    return sum(1 if ord(character) < 128 else 2 for character in value)


def require_text(value: Any, field: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MenuManagerError(f"{field} 必须是非空字符串")
    if maximum is not None and weighted_length(value) > maximum:
        raise MenuManagerError(f"{field} 超过 QQ 的 {maximum} 字符限制")
    return value


def load_credentials(bot_id: str | None) -> Credentials:
    simple_app_id = os.environ.get("QQ_APP_ID", "").strip()
    simple_secret = os.environ.get("QQ_APP_SECRET", "").strip()
    if simple_app_id or simple_secret:
        if not simple_app_id or not simple_secret:
            raise MenuManagerError("QQ_APP_ID 与 QQ_APP_SECRET 必须同时填写")
        if bot_id is not None and bot_id != simple_app_id:
            raise MenuManagerError(f"QQ_APP_ID 与 --bot-id {bot_id} 不一致")
        return Credentials(app_id=simple_app_id, client_secret=simple_secret)

    raw_bots = os.environ.get("QQ_BOTS", "")
    if not raw_bots:
        try:
            from bot_tools.bot_credentials import BotCredentialStore
            from bot_tools.storage import Store, ToolError

            stored = BotCredentialStore(Store()).runtime()
        except (ImportError, OSError, ToolError) as exc:
            raise MenuManagerError("无法读取后台保存的机器人凭据") from exc
        candidates = [item for item in stored if bot_id is None or item.app_id == bot_id]
        if bot_id is not None and not candidates:
            raise MenuManagerError(f"后台凭据中找不到已启用的 AppID {bot_id}")
        if not candidates:
            raise MenuManagerError("请先在网页后台添加并启用机器人")
        if len(candidates) != 1:
            raise MenuManagerError("后台包含多个已启用机器人，请用 --bot-id 指定 AppID")
        return Credentials(candidates[0].app_id, candidates[0].secret)

    try:
        bots = json.loads(raw_bots)
    except json.JSONDecodeError as exc:
        raise MenuManagerError("QQ_BOTS 不是有效 JSON") from exc

    if not isinstance(bots, list) or not bots:
        raise MenuManagerError("QQ_BOTS 必须是至少包含一个机器人的 JSON 数组")

    candidates = [bot for bot in bots if isinstance(bot, dict)]
    if bot_id is not None:
        candidates = [bot for bot in candidates if str(bot.get("id", "")) == bot_id]
        if not candidates:
            raise MenuManagerError(f"QQ_BOTS 中找不到 AppID {bot_id}")
    elif len(candidates) != 1:
        raise MenuManagerError("QQ_BOTS 包含多个机器人，请用 --bot-id 指定 AppID")

    bot = candidates[0]
    app_id = require_text(str(bot.get("id", "")), "QQ_BOTS.id")
    client_secret = require_text(bot.get("secret"), "QQ_BOTS.secret")
    return Credentials(app_id=app_id, client_secret=client_secret)


def validate_menu_item(item: Any, field: str, *, submenu: bool = False) -> None:
    if not isinstance(item, dict):
        raise MenuManagerError(f"{field} 必须是对象")

    item_type = require_text(item.get("type"), f"{field}.type")
    allowed_types = {"send_message", "link"} if submenu else {
        "send_message",
        "link",
        "menu",
        "switch",
    }
    if item_type not in allowed_types:
        raise MenuManagerError(f"{field}.type 不受支持：{item_type}")

    require_text(item.get("name"), f"{field}.name", 14 if submenu else 10)
    if item_type == "send_message":
        require_text(item.get("send_message"), f"{field}.send_message")
    elif item_type == "link":
        link = require_text(item.get("link"), f"{field}.link")
        if not link.startswith("https://"):
            raise MenuManagerError(f"{field}.link 必须以 https:// 开头")
    elif item_type == "menu":
        children = item.get("sub_menu_items")
        if not isinstance(children, list) or not 1 <= len(children) <= 5:
            raise MenuManagerError(f"{field}.sub_menu_items 必须包含 1 到 5 项")
        for index, child in enumerate(children):
            validate_menu_item(child, f"{field}.sub_menu_items[{index}]", submenu=True)
    else:
        switch = item.get("switch")
        if not isinstance(switch, dict):
            raise MenuManagerError(f"{field}.switch 必须是对象")
        require_text(switch.get("switch_id"), f"{field}.switch.switch_id")
        if "default" in switch and not isinstance(switch["default"], bool):
            raise MenuManagerError(f"{field}.switch.default 必须是布尔值")


def validate_panel_item(item: Any, field: str) -> None:
    if not isinstance(item, dict):
        raise MenuManagerError(f"{field} 必须是对象")

    item_type = require_text(item.get("type"), f"{field}.type")
    if item_type not in {"command", "link"}:
        raise MenuManagerError(f"{field}.type 只能是 command 或 link")
    require_text(item.get("name"), f"{field}.name", 14)

    description = item.get("desc", "")
    if not isinstance(description, str) or weighted_length(description) > 30:
        raise MenuManagerError(f"{field}.desc 必须是不超过 30 字符的字符串")
    if "only_admin" in item and not isinstance(item["only_admin"], bool):
        raise MenuManagerError(f"{field}.only_admin 必须是布尔值")
    if item_type == "link":
        link = require_text(item.get("link"), f"{field}.link")
        if not link.startswith("https://"):
            raise MenuManagerError(f"{field}.link 必须以 https:// 开头")


def load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise MenuManagerError(f"无法读取配置文件 {CONFIG_PATH.name}") from exc
    except json.JSONDecodeError as exc:
        raise MenuManagerError(f"{CONFIG_PATH.name} 不是有效 JSON") from exc

    if not isinstance(config, dict):
        raise MenuManagerError(f"{CONFIG_PATH.name} 顶层必须是对象")

    custom_menu = config.get("custom_menu")
    if custom_menu is not None:
        if not isinstance(custom_menu, dict):
            raise MenuManagerError("custom_menu 必须是对象或 null")
        menu_items = custom_menu.get("items")
        if not isinstance(menu_items, list) or not 1 <= len(menu_items) <= 10:
            raise MenuManagerError("custom_menu.items 必须包含 1 到 10 项")
        for index, item in enumerate(menu_items):
            validate_menu_item(item, f"custom_menu.items[{index}]")

    panels = config.get("panels")
    if not isinstance(panels, list) or len(panels) > 20:
        raise MenuManagerError("panels 必须是最多包含 20 项的数组")

    seen_markers: set[tuple[str, str]] = set()
    for index, definition in enumerate(panels):
        field = f"panels[{index}]"
        if not isinstance(definition, dict):
            raise MenuManagerError(f"{field} 必须是对象")

        scope = require_text(definition.get("scope"), f"{field}.scope")
        if scope not in {"c2c", "group", "channel", "dm"}:
            raise MenuManagerError(f"{field}.scope 不受支持：{scope}")
        target_type = definition.get("target_type", "all")
        if target_type not in {"all", "specific"}:
            raise MenuManagerError(f"{field}.target_type 只能是 all 或 specific")
        if scope in {"channel", "dm"} and target_type != "all":
            raise MenuManagerError(f"{field}: channel/dm 只支持 target_type=all")

        panel = definition.get("panel")
        if not isinstance(panel, dict):
            raise MenuManagerError(f"{field}.panel 必须是对象")
        items = panel.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise MenuManagerError(f"{field}.panel.items 必须包含 1 到 20 项")
        for item_index, item in enumerate(items):
            validate_panel_item(item, f"{field}.panel.items[{item_index}]")

        remark = require_text(panel.get("remark"), f"{field}.panel.remark", 255)
        if not remark.startswith(MANAGED_REMARK_PREFIX):
            raise MenuManagerError(
                f"{field}.panel.remark 必须以 {MANAGED_REMARK_PREFIX} 开头，供脚本安全识别"
            )
        marker = (scope, remark)
        if marker in seen_markers:
            raise MenuManagerError(f"重复的面板标记：{scope}/{remark}")
        seen_markers.add(marker)

        if target_type == "specific":
            target_field = "user_openids" if scope == "c2c" else "group_openids"
            targets = definition.get(target_field)
            if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
                raise MenuManagerError(f"{field}.{target_field} 必须包含 1 到 20 个 openid")
            for target_index, target in enumerate(targets):
                require_text(target, f"{field}.{target_field}[{target_index}]")

    return config


def decode_json(payload: bytes, context: str) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MenuManagerError(f"{context} 返回了无法解析的响应") from exc
    if not isinstance(result, dict):
        raise MenuManagerError(f"{context} 返回的 JSON 顶层不是对象")
    return result


class QQClient:
    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.access_token = self._get_access_token()

    def _get_access_token(self) -> str:
        body = {
            "appId": self.credentials.app_id,
            "clientSecret": self.credentials.client_secret,
        }
        response = self._request_url("POST", TOKEN_URL, body, authenticated=False)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise MenuManagerError("QQ 访问凭证响应中缺少 access_token")
        return token

    def _request_url(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json", "User-Agent": "nonebot-qq-menu-manager/1.0"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if authenticated:
            headers["Authorization"] = f"QQBot {self.access_token}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read(65537)
                if len(payload) > 65536:
                    raise MenuManagerError("QQ API 响应过大，已中止解析")
                result = decode_json(payload, f"QQ API {method} {urllib.parse.urlsplit(url).path}")
        except urllib.error.HTTPError as exc:
            payload = exc.read(65536)
            try:
                details = decode_json(payload, "QQ API 错误")
            except MenuManagerError:
                details = {}
            trace_id = details.get("trace_id") or exc.headers.get("X-Tps-trace-ID")
            err_code = details.get("err_code", "unknown")
            message = details.get("message", exc.reason)
            trace_suffix = f"，trace_id={trace_id}" if trace_id else ""
            raise MenuManagerError(
                f"QQ API 请求失败（HTTP {exc.code}，err_code={err_code}{trace_suffix}）：{message}"
            ) from exc
        except urllib.error.URLError as exc:
            raise MenuManagerError(f"无法连接 QQ API：{exc.reason}") from exc

        err_code = result.get("err_code")
        if err_code not in (None, 0):
            trace_id = result.get("trace_id")
            trace_suffix = f"，trace_id={trace_id}" if trace_id else ""
            raise MenuManagerError(
                f"QQ API 返回错误（err_code={err_code}{trace_suffix}）：{result.get('message', '未知错误')}"
            )
        return result

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        return self._request_url(method, url, body)

    def get_panels(self, scope: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            query: dict[str, Any] = {"scope": scope, "limit": 50}
            if cursor:
                query["cursor"] = cursor
            page = self.request("GET", "/v2/panels", query=query)
            page_records = page.get("records", [])
            if not isinstance(page_records, list):
                raise MenuManagerError("QQ 面板列表响应中的 records 不是数组")
            records.extend(record for record in page_records if isinstance(record, dict))

            next_cursor = page.get("next_cursor", "")
            if page.get("is_end") is True or not isinstance(next_cursor, str) or not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise MenuManagerError("QQ 面板列表返回了重复游标")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return records


def normalize_panel(panel: Any) -> dict[str, Any]:
    if not isinstance(panel, dict):
        return {"items": [], "remark": ""}
    normalized_items: list[dict[str, Any]] = []
    for item in panel.get("items", []):
        if not isinstance(item, dict):
            continue
        normalized_item = {
            "type": item.get("type", ""),
            "name": item.get("name", ""),
            "desc": item.get("desc", ""),
            "only_admin": bool(item.get("only_admin", False)),
        }
        if item.get("type") == "link":
            normalized_item["link"] = item.get("link", "")
        normalized_items.append(normalized_item)
    return {"items": normalized_items, "remark": panel.get("remark", "")}


def normalize_menu(menu: Any) -> Any:
    if not isinstance(menu, dict):
        return None

    def normalize_item(item: Any) -> Any:
        if not isinstance(item, dict):
            return None
        item_type = item.get("type", "")
        normalized: dict[str, Any] = {
            "type": item_type,
            "name": item.get("name", ""),
        }
        if item_type == "send_message":
            normalized["send_message"] = item.get("send_message", "")
        elif item_type == "link":
            normalized["link"] = item.get("link", "")
        elif item_type == "menu":
            normalized["sub_menu_items"] = [
                normalize_item(child) for child in item.get("sub_menu_items", [])
            ]
        elif item_type == "switch":
            switch = item.get("switch") if isinstance(item.get("switch"), dict) else {}
            normalized["switch"] = {
                "switch_id": switch.get("switch_id", ""),
                "default": bool(switch.get("default", False)),
            }
        return normalized

    return [normalize_item(item) for item in menu.get("items", [])]


def collect_state(client: QQClient, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    current_menu = client.request("GET", "/v2/menu")
    scopes = sorted({definition["scope"] for definition in config["panels"]})
    panels_by_scope = {scope: client.get_panels(scope) for scope in scopes}
    return current_menu, panels_by_scope


def show_status(client: QQClient, config: dict[str, Any]) -> None:
    current_menu, panels_by_scope = collect_state(client, config)
    menu = current_menu.get("menu")
    menu_items = menu.get("items", []) if isinstance(menu, dict) else []
    if menu_items:
        names = ", ".join(str(item.get("name", "?")) for item in menu_items if isinstance(item, dict))
        print(f"单聊自定义菜单：已配置 {len(menu_items)} 项（{names}）")
    else:
        print("单聊自定义菜单：未配置")

    for scope, records in panels_by_scope.items():
        print(f"{scope} 指令面板：{len(records)} 个")
        for record in records:
            panel = record.get("panel") if isinstance(record.get("panel"), dict) else {}
            names = ", ".join(
                str(item.get("name", "?"))
                for item in panel.get("items", [])
                if isinstance(item, dict)
            )
            print(
                f"  - {record.get('panel_id', '?')} | {record.get('target_type', '?')} | "
                f"{panel.get('remark', '')} | {names}"
            )


def verify_sync(client: QQClient, config: dict[str, Any]) -> None:
    """Read back platform state; an HTTP write alone is not proof of sync."""
    current_menu, panels_by_scope = collect_state(client, config)
    desired_menu = config.get("custom_menu")
    if desired_menu is not None and normalize_menu(current_menu.get("menu")) != normalize_menu(desired_menu):
        raise MenuManagerError("同步后回读的单聊菜单与配置不一致，请稍后执行 status 检查。")
    for definition in config["panels"]:
        expected = definition["panel"]
        matches = [record for record in panels_by_scope[definition["scope"]]
                   if isinstance(record.get("panel"), dict) and record["panel"].get("remark") == expected["remark"]]
        if len(matches) != 1 or normalize_panel(matches[0].get("panel")) != normalize_panel(expected):
            raise MenuManagerError(f"同步后回读的 {definition['scope']} 指令面板与配置不一致，请稍后执行 status 检查。")
        if matches[0].get("target_type") != definition.get("target_type", "all"):
            raise MenuManagerError(f"同步后回读的 {definition['scope']} 面板作用范围不一致。")
    print("平台回读验证通过：菜单与受管指令面板均与本地配置一致。")


def sync(client: QQClient, config: dict[str, Any], *, force_menu: bool) -> None:
    current_menu, panels_by_scope = collect_state(client, config)
    actions: list[tuple[str, Any, Any]] = []

    desired_menu = config.get("custom_menu")
    if desired_menu is not None:
        current_menu_value = current_menu.get("menu")
        current_items = normalize_menu(current_menu_value)
        desired_items = normalize_menu(desired_menu)
        if current_items != desired_items:
            if current_items and not force_menu:
                raise MenuManagerError(
                    "QQ 已有不同的单聊自定义菜单；为避免覆盖，请先查看 '.\\menu.ps1 status'，"
                    "确认后使用 '.\\menu.ps1 sync -ForceMenu'"
                )
            actions.append(("menu", None, desired_menu))

    for definition in config["panels"]:
        scope = definition["scope"]
        desired_panel = definition["panel"]
        remark = desired_panel["remark"]
        matches = [
            record
            for record in panels_by_scope[scope]
            if isinstance(record.get("panel"), dict) and record["panel"].get("remark") == remark
        ]
        if len(matches) > 1:
            raise MenuManagerError(f"QQ 中存在多个同名受管面板：{scope}/{remark}，请先手工清理")
        if matches:
            existing = matches[0]
            if existing.get("target_type") != definition.get("target_type", "all"):
                raise MenuManagerError(
                    f"已有面板 {existing.get('panel_id')} 的 target_type 与配置不一致；"
                    "该字段不能通过更新接口修改"
                )
            if normalize_panel(existing.get("panel")) != normalize_panel(desired_panel):
                actions.append(("update", existing.get("panel_id"), definition))
        else:
            actions.append(("create", None, definition))

    if not actions:
        print("QQ 菜单与指令面板已是最新配置，无需修改。")
        return

    for action, panel_id, payload in actions:
        if action == "menu":
            response = client.request("PUT", "/v2/menu", {"menu": payload})
            print(f"已同步单聊自定义菜单（version={response.get('version', '?')}）。")
        elif action == "create":
            response = client.request("POST", "/v2/panels", payload)
            print(f"已创建 {payload['scope']} 指令面板（panel_id={response.get('panel_id', '?')}）。")
        else:
            safe_panel_id = urllib.parse.quote(str(panel_id), safe="")
            response = client.request(
                "PUT",
                f"/v2/panels/{safe_panel_id}",
                {"panel": payload["panel"]},
            )
            print(f"已更新 {payload['scope']} 指令面板（version={response.get('version', '?')}）。")
    verify_sync(client, config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 QQ 自定义菜单和指令面板")
    parser.add_argument("action", choices=("status", "sync"))
    parser.add_argument("--bot-id", help="QQ_BOTS 中存在多个机器人时指定 AppID")
    parser.add_argument(
        "--force-menu",
        action="store_true",
        help="允许覆盖已存在且与 qq-menu.json 不同的单聊自定义菜单",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config()
        credentials = load_credentials(args.bot_id)
        client = QQClient(credentials)
        if args.action == "status":
            show_status(client, config)
        else:
            sync(client, config, force_menu=args.force_menu)
    except MenuManagerError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
