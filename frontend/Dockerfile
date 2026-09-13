# Frontend: сборка Vite -> раздача nginx (+ реверс-прокси на api).
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Кэш npm переживает сборки: при смене lock-файла пакеты не качаются заново.
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

# nginx без root: образ nginx-unprivileged работает от uid 101 и слушает 8080 —
# порты ниже 1024 обычной учётке недоступны.
FROM nginxinc/nginx-unprivileged:1.29-alpine
# Конфиг — шаблон: при старте образ подставляет в него DNS контейнера
# (${NGINX_LOCAL_RESOLVERS}) и пишет результат в conf.d. Класть сразу в conf.d
# нельзя: файл от root nginx без root перезаписать не сможет.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
