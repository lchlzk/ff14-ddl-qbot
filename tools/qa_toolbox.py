"""Generate renderer QA samples; does not access credentials or live bot state."""
import io
import sys
from pathlib import Path

from PIL import Image
from bot_tools.media import render_gif, render_tex

target = Path(sys.argv[1])
target.mkdir(parents=True,exist_ok=True)
(target/"formula.png").write_bytes(render_tex(r"\frac{-b\pm\sqrt{b^2-4ac}}{2a}"))
gif = render_gif("今天也要开心")
(target/"text.gif").write_bytes(gif)
with Image.open(io.BytesIO(gif)) as image:
    image.convert("RGB").save(target/"text-preview.png")
print("Generated formula.png, text.gif and text-preview.png")
