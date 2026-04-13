# syntax=docker/dockerfile:1.7

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY generate-cert.sh /docker-entrypoint.d/10-generate-cert.sh

RUN chmod +x /docker-entrypoint.d/10-generate-cert.sh
