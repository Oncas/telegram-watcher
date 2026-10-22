FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libnotify-bin util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/watcher
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund \
    && ln -s /opt/watcher/node_modules /node_modules

ENV PATH=/opt/watcher/node_modules/.bin:$PATH \
    NPM_CONFIG_CACHE=/app/.npm-cache \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app
COPY package.json ./
COPY src ./src
COPY listeners/example.ts ./listeners/example.ts
RUN chmod +x /app/src/cli.ts \
    && ln -s /app/src/cli.ts /usr/local/bin/telegram-watcher

CMD ["telegram-watcher", "--help"]
