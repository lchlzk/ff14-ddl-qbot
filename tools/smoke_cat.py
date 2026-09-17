import asyncio

from bot_tools.media import cat_picture, checked_image
from bot_tools.storage import ToolError


async def main():
    try:
        image = await cat_picture()
        print("Cat API OK:",len(checked_image(image)),"bytes")
    except ToolError as exc:
        print("Cat API unavailable:",str(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    asyncio.run(main())
