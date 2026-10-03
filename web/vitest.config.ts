import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// 순수 함수·API 클라이언트는 node, 화면 테스트는 파일 머리의 @vitest-environment jsdom 으로
export default defineConfig({
  plugins: [react()],
  test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"], restoreMocks: true },
});
