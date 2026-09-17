from nonebot import on_command

from message_ui import panel


ping = on_command("ping", priority=10, block=True)


@ping.handle()
async def handle_ping() -> None:
    await ping.finish(panel("机器人在线", "连接正常，可以继续使用。", icon="🟢"))
