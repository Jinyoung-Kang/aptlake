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
  // 페이지는 지연 로딩 청크, ECharts(필요 모듈만 등록)는 공용 청크 하나(약 690KB, gzip 230KB)
  build: { sourcemap: false, chunkSizeWarningLimit: 800 },
});
