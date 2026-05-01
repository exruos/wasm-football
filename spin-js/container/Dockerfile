ARG RUNTIME=node

FROM node:25-slim AS deps-node
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM oven/bun:latest AS deps-bun
WORKDIR /app
COPY package.json bun.lockb* ./
RUN bun install --production

FROM denoland/deno:latest AS deps-deno
WORKDIR /app
COPY --from=deps-node /app/node_modules ./node_modules
COPY package.json ./
RUN deno cache --node-modules-dir=manual container/src/main.js || true

FROM node:25-slim AS runtime-node
WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8088
COPY --from=deps-node /app/node_modules ./node_modules
COPY package.json ./package.json
COPY src ./src
COPY container ./container
EXPOSE 8088
CMD ["node", "container/src/main.js"]

FROM oven/bun:latest AS runtime-bun
WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8088
COPY --from=deps-bun /app/node_modules ./node_modules
COPY package.json ./package.json
COPY src ./src
COPY container ./container
EXPOSE 8088
CMD ["bun", "container/src/main.js"]

FROM denoland/deno:latest AS runtime-deno
WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8088
COPY --from=deps-node /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src
COPY container ./container
EXPOSE 8088
CMD ["deno", "run", "--allow-net", "--allow-env", "--node-modules-dir=manual", "container/src/main.js"]

FROM runtime-${RUNTIME}