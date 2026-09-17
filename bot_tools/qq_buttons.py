"""Small QQ inline-keyboard builders with sender-only command actions."""
from __future__ import annotations

from collections.abc import Sequence

from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)


def command_keyboard(
    actions: Sequence[tuple[str, str, int]],
    user_id: str,
    *,
    columns: int = 3,
) -> MessageKeyboard | None:
    """Build up to 15 sender-only command buttons in a compact keyboard."""
    if not actions:
        return None
    if not 1 <= columns <= 5:
        raise ValueError("keyboard columns must be between 1 and 5")
    if len(actions) > 15:
        raise ValueError("a command keyboard supports at most 15 actions")
    buttons: list[Button] = []
    for index, (label, command, style) in enumerate(actions):
        buttons.append(Button(
            id=f"command_{index}",
            render_data=RenderData(label=label, visited_label=label, style=style),
            action=Action(
                type=2,
                permission=Permission(type=0, specify_user_ids=[str(user_id)]),
                data=command,
                reply=False,
                enter=True,
                unsupport_tips=f"客户端不支持按钮，请手动发送：{command}",
            ),
        ))
    rows = [
        InlineKeyboardRow(buttons=buttons[index:index + columns])
        for index in range(0, len(buttons), columns)
    ]
    return MessageKeyboard(content=InlineKeyboard(rows=rows))


def pagination_keyboard(
    previous_command: str | None,
    next_command: str | None,
    user_id: str,
) -> MessageKeyboard | None:
    """Build previous/next command buttons restricted to the requesting user."""
    actions = [
        (label, command, style)
        for label, command, style in (
            ("上一页", previous_command, 0),
            ("下一页", next_command, 1),
        )
        if command is not None
    ]
    return command_keyboard(actions, user_id, columns=2)
