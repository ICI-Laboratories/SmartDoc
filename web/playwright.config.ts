import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  webServer: [
    {command: "../.venv/bin/python ../tests/browser_server.py", url:"http://127.0.0.1:8046/openapi.json", reuseExistingServer:false,env:{SARA_DATABASE_URL:process.env.SARA_E2E_DATABASE_URL??""}},
    {command: "npm run preview -- --port 4173",url: "http://127.0.0.1:4173",reuseExistingServer: false}
  ],
  timeout: 45000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  reporter: "list",
});
