FROM node:22-alpine AS builder

WORKDIR /app

ARG BACKEND_URL=http://backend:8686
ENV BACKEND_URL=${BACKEND_URL}

COPY package.json ./
# 配置国内 npmmirror 镜像源，防止 ECONNRESET 网络重置中断
RUN npm config set registry https://registry.npmmirror.com && \
    npm install --legacy-peer-deps

COPY . .
RUN npm run build

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    BACKEND_URL=http://backend:8686

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/next.config.js ./

EXPOSE 3000

CMD ["npm", "start"]