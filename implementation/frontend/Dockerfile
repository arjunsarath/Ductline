# Frontend dev image — Vite serves with HMR.
# Production build is out of scope for v1 (no hosting per SOLUTION-DESIGN §2.2).
FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
