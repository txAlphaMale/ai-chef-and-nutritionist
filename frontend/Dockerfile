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
# The runtime shell: server.js serves the built static files AND reverse-
# proxies /api/* and /health to the backend, so a browser on the LAN only
# ever reaches and trusts this one origin. Its two dependencies are kept
# out of the app's own package.json because none of React/Vite/ZXing is
# needed once the Vite build has produced static output.
#
# Installed LOCALLY into /app/node_modules, not with `npm install -g`.
#
# This was a global install, and it never worked. Node does not search
# npm's global directory (/usr/local/lib/node_modules) when resolving
# require() -- NODE_PATH is not set in this base image and the legacy
# GLOBAL_FOLDERS list does not include it. So the container started,
# logged that it was serving HTTPS, and then died on
# `Cannot find module 'http-proxy'` in a restart loop that never showed
# up as a failed build or an unhealthy container. Only redirect-server.js
# kept running, because it uses nothing but Node built-ins -- which is
# why port 5173 still redirected to a 5174 that had nothing listening.
#
# A local install is the ordinary resolution path and needs no NODE_PATH
# trick to work.
COPY runtime-deps/package.json runtime-deps/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/dist ./dist
COPY docker-entrypoint.sh .
COPY redirect-server.js .
COPY server.js .
RUN chmod +x docker-entrypoint.sh

# Fail the BUILD if server.js cannot load, instead of shipping an image
# that crash-loops at runtime.
#
# The global-install bug above was invisible for exactly one reason:
# nothing ever tried to load this file until a user hit the site. The
# build passed, the container reported healthy (its healthcheck only
# tests the backend), and the only evidence was a restart counter nobody
# was reading. One line here converts that whole class of failure into a
# red build.
#
# `--check` parses without executing, so no port is bound; the requires
# are asserted separately.
RUN node --check server.js \
    && node --check redirect-server.js \
    && node -e "require('http-proxy'); require('serve-handler'); console.log('runtime deps resolve OK')"

# Backlog B15.1 (2026-08-01) -- shared with the backend container via
# the chef-tls volume (read-only here; the backend writes it). See
# docker-entrypoint.sh and backend/app/services/tls_service.py.
#
# No VOLUME directive -- see backend/Dockerfile for why (it does not make
# the Compose mount work, and it hands anyone running the image directly
# a surprise anonymous volume).
RUN mkdir -p /app/tls

# Same fixed uid as the backend image, deliberately: the backend writes
# the TLS key 0600 onto the shared chef-tls volume and this container has
# to read it. Matching uids is what makes that readable without widening
# the key's permissions.
RUN groupadd --gid 10001 chef \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin chef

EXPOSE 5173 5174
# Backlog B15.1 -- docker-entrypoint.sh now decides plain-HTTP vs.
# HTTPS itself (by checking for a certificate on /app/tls) and starts
# `serve` with the right flags, instead of a static CMD -- see that
# script's own comments. No CMD args needed; the script is
# self-contained and doesn't read "$@".
ENTRYPOINT ["./docker-entrypoint.sh"]
