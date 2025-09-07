# Build stage
FROM node:22-alpine AS builder

ARG API_BASE_URL=http://localhost:8000

WORKDIR /app

COPY frontend/courses-front/package*.json ./

RUN npm install --legacy-peer-deps

COPY frontend/courses-front .

ENV VITE_API_BASE_URL=$API_BASE_URL

RUN npm run build

# Production stage
FROM ghcr.io/nginxinc/nginx-unprivileged:stable-alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
