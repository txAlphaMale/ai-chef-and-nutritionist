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
EXPOSE 5173
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["serve", "-s", "dist", "-l", "5173"]
