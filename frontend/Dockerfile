# syntax=docker/dockerfile:1

# ---------- base ----------
FROM node:24-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# ---------- deps: install node_modules from the lockfile ----------
FROM base AS deps
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---------- build ----------
FROM base AS build
# Rewrites (/api/* → backend) are resolved at build time for the standalone output.
ARG BACKEND_INTERNAL_URL=http://backend:8000
ARG NEXT_PUBLIC_APP_NAME="Домашняя выпечка"
ENV BACKEND_INTERNAL_URL=${BACKEND_INTERNAL_URL} \
    NEXT_PUBLIC_APP_NAME=${NEXT_PUBLIC_APP_NAME}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `public/` must exist for the runner stage COPY below.
RUN mkdir -p public && npm run build

# ---------- runner ----------
FROM base AS runner
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S -g 1001 nodejs \
    && adduser -S -u 1001 -G nodejs nextjs

COPY --from=build --chown=nextjs:nodejs /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/login" > /dev/null || exit 1

CMD ["node", "server.js"]
