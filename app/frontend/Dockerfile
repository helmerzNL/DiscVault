FROM nginx:alpine

ARG BUILD_VERSION=dev

COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY i18n/ /usr/share/nginx/html/i18n/
COPY js/ /usr/share/nginx/html/js/
COPY logo.svg /usr/share/nginx/html/logo.svg
COPY icon.svg /usr/share/nginx/html/icon.svg
COPY apple-touch-icon.png /usr/share/nginx/html/apple-touch-icon.png
COPY favicon-32.png /usr/share/nginx/html/favicon-32.png
COPY favicon-192.png /usr/share/nginx/html/favicon-192.png
COPY manifest.json /usr/share/nginx/html/manifest.json
COPY service-worker.js /usr/share/nginx/html/service-worker.js
RUN printf '{"version":"%s"}\n' "$BUILD_VERSION" > /usr/share/nginx/html/version.json
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
