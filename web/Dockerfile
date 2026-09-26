# syntax=docker/dockerfile:1.7
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npx tsc -b && npx vite build
# nginx gzip_static 용으로 미리 최고 압축(9)해 둔다 — 요청마다 압축하는 CPU 를 없애고 더 작게
RUN node -e 'const fs=require("fs"),z=require("zlib"),p=require("path");(function w(d){for(const f of fs.readdirSync(d)){const x=p.join(d,f);if(fs.statSync(x).isDirectory())w(x);else if(/\.(js|css|html|svg|json)$/.test(f)&&fs.statSync(x).size>1024)fs.writeFileSync(x+".gz",z.gzipSync(fs.readFileSync(x),{level:9}))}})("dist")'

FROM nginxinc/nginx-unprivileged:1.29-alpine
# 기본 사이트 설정 제거, 메인 설정 교체, 사이트 설정은 템플릿(envsubst: WEB_API_KEY 만 치환)
USER root
RUN rm -f /etc/nginx/conf.d/default.conf
USER 101
COPY nginx.main.conf /etc/nginx/nginx.conf
COPY templates /etc/nginx/templates
ENV NGINX_ENVSUBST_OUTPUT_DIR=/tmp NGINX_ENVSUBST_FILTER=WEB_API_KEY
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
