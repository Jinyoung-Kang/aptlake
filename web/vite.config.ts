import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버는 공개 API 만 프록시한다 (관리 API :8611 은 브라우저에서 접근하지 않음)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3611,
    proxy: {
      "/v1": "http://127.0.0.1:8610",
      "/docs": "http://127.0.0.1:8610",
      "/openapi.json": "http://127.0.0.1:8610",
    },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1200 },
});
