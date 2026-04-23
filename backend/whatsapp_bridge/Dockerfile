FROM node:20-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY backend/whatsapp_bridge/package.json backend/whatsapp_bridge/package-lock.json ./
RUN npm ci --omit=dev

COPY backend/whatsapp_bridge/ ./

ENV CHROME_BIN=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

EXPOSE 3001

CMD ["npm", "start"]
