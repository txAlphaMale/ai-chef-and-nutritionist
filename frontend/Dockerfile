FROM node:22-slim AS build
WORKDIR /app
# package-lock.json is copied too, and `npm ci` (not `npm install`) is used:
# ci installs exactly the locked tree and fails if the lockfile and
# package.json disagree, so a build produces the same dependency set every
# time. `npm install` silently re-resolved semver ranges on every build.
COPY package.json package-lock.json ./
RUN npm ci
# .dockerignore is what makes this safe -- without it this line copies the
# host's node_modules over the one npm just installed. See that file.
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
# Author-reported 2026-08-03: replaced the plain `serve` static server
# with a small custom one (server.js) that ALSO reverse-proxies /api/*
# and /health to the backend container -- so a browser anywhere on the
# LAN only ever needs to reach and trust this one origin, never the
# backend's directly. See server.js's own module docstring for the full
# rationale. Global install, same as `serve` was -- neither of these
# (nor `serve` before them) belongs in package.json, since none of this
# app's actual npm dependencies are needed after the Vite build produces
# static output; only this runtime shell needs them.
# Pinned. These are the runtime shell (static serving + reverse proxy);
# unpinned globals mean a future rebuild can silently pull a breaking
# major of either one into a container nobody is watching.
RUN npm install -g http-proxy@1.18.1 serve-handler@6.1.5
COPY --from=build /app/dist ./dist
COPY docker-entrypoint.sh .
COPY redirect-server.js .
COPY server.js .
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
