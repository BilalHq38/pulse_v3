# syntax=docker/dockerfile:1.4
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit

COPY frontend/ ./
ARG REACT_APP_BACKEND_URL=http://localhost:8000
ENV REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL}
ARG REACT_APP_DEMO_MODE=false
ENV REACT_APP_DEMO_MODE=${REACT_APP_DEMO_MODE}
# Faster production builds (smaller output, less work for webpack)
ENV CI=true
ENV GENERATE_SOURCEMAP=false
ENV DISABLE_ESLINT_PLUGIN=true

RUN --mount=type=cache,target=/app/node_modules/.cache \
    npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
