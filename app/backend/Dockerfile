FROM python:3.12-slim

ARG BUILD_VERSION=dev
ARG BUILD_SHA=unknown

ENV BUILD_VERSION=${BUILD_VERSION} \
    BUILD_SHA=${BUILD_SHA} \
    DISCVAULT_IMAGE_VERSION=${BUILD_VERSION} \
    DISCVAULT_IMAGE_SHA=${BUILD_SHA} \
    DISCVAULT_BACKEND_VERSION=${BUILD_VERSION} \
    DISCVAULT_BUILD_SHA=${BUILD_SHA}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "1800", "app:app"]
