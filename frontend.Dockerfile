FROM node:22-alpine

WORKDIR /app

COPY frontend/courses-front/package*.json ./

RUN npm install --legacy-peer-deps

COPY frontend/courses-front .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
