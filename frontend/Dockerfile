FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# Finish package extraction before executing native postinstall binaries.
# A separate layer avoids ETXTBSY on rootless Docker with the vfs driver.
RUN npm ci --ignore-scripts --no-audit --no-fund
RUN npm rebuild
COPY frontend/ ./
ENV VITE_USE_MOCK=false VITE_API_BASE_URL=/api
RUN npm run build

FROM nginx:1.28-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
