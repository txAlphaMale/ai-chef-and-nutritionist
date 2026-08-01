FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Backlog B15.1 (2026-08-01) -- shared with the backend container via
# the chef-tls volume (read-only here; the backend writes it). See
# docker-entrypoint.sh and backend/app/services/tls_service.py.
RUN mkdir -p /app/tls
VOLUME ["/app/tls"]

EXPOSE 5173 5174
# Backlog B15.1 -- docker-entrypoint.sh now decides plain-HTTP vs.
# HTTPS itself (by checking for a certificate on /app/tls) and starts
# `serve` with the right flags, instead of a static CMD -- see that
# script's own comments. No CMD args needed; the script is
# self-contained and doesn't read "$@".
ENTRYPOINT ["./docker-entrypoint.sh"]
