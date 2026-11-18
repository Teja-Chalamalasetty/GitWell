# syntax=docker/dockerfile:1

# ---- build the static site --------------------------------------------------------------------
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Read by Vite at BUILD time; the browser calls this URL.
ARG VITE_API_BASE_URL=http://localhost:8000/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---- serve it ---------------------------------------------------------------------------------
FROM nginx:alpine
# The API's origin, allowed by the Content-Security-Policy (connect-src). The nginx image renders
# /etc/nginx/templates/*.template with the container's environment at start-up.
ENV API_ORIGIN=http://localhost:8000
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
