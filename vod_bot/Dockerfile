FROM python:3.11-slim

WORKDIR /app

# Playwright の Chromium が必要とするシステム依存ライブラリをインストール
RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

COPY vod_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright の Chromium をインストール
RUN playwright install chromium

# vod_bot と news_bot 共通の utils/ を先に、続けて vod_bot 本体をコピーする
# （ビルドコンテキストはリポジトリルート。vod_bot/Dockerfile 参照）
COPY utils/ ./utils/
COPY vod_bot/ .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "600", "main:app"]
