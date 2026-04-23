FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
ARG REACT_APP_BACKEND_URL=http://localhost:8000
ENV REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL}
ARG REACT_APP_DEMO_MODE=false
ENV REACT_APP_DEMO_MODE=${REACT_APP_DEMO_MODE}
RUN npm run build

FROM node:20-alpine

WORKDIR /app
RUN npm install -g serve@14

COPY --from=build /app/build ./build

EXPOSE 3000

CMD ["serve", "-s", "build", "-l", "3000"]
