import { defineConfig } from "@playwright/test";

/**
 * Phase 14 e2e（手册第六项）。前置服务（e2e/README 说明）：
 * - 后端 uvicorn :8000（隔离 STORAGE__TMP_DIR / REDIS__DB=13）
 * - Celery worker（同 env，solo 池即可）
 * - 前端 next dev :3000（本配置自动拉起）
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  retries: 0,
  workers: 1, // 全流程走真实后端，串行执行
  use: {
    // 3001：本机 3000 常被其它应用占用；后端 e2e 实例需以
    // API__CORS_ORIGINS 环境变量放行 localhost:3001
    baseURL: "http://localhost:3001",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- -p 3001",
    url: "http://localhost:3001",
    reuseExistingServer: false, // 严禁复用——3000 曾被无关应用占用导致误测
    timeout: 60_000,
  },
});
