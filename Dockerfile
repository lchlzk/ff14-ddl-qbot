FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk git \
    # media.font uses this complete regular CJK collection, not the bold/serif families.
    && test -s /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    && rm -f /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc \
             /usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc \
             /usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc \
    && rm -rf /var/lib/apt/lists/*

COPY tools/prune_runtime.py /tmp/prune_runtime.py
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --no-compile --requirement requirements.txt \
    && python /tmp/prune_runtime.py

COPY requirements-first-party.txt requirements-plugins.txt ./
RUN python -m pip install --no-cache-dir --no-compile \
        --requirement requirements.txt \
        --requirement requirements-first-party.txt \
        --requirement requirements-plugins.txt \
    && python -m pip check \
    && python /tmp/prune_runtime.py \
    && python -m playwright install --with-deps chromium \
    && chown -R bot:bot /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

COPY bot.py ./
COPY message_ui.py ./
COPY qq_menu.py qq-menu.json ./
COPY third_party_plugins.json ./
COPY LICENSES ./LICENSES
COPY plugins ./plugins
COPY bot_tools ./bot_tools
COPY admin_web ./admin_web

USER bot

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

CMD ["python", "bot.py"]
