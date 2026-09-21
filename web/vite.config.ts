import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";
export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8043",
      "/auth": "http://127.0.0.1:8043",
      "/session": "http://127.0.0.1:8043",
    },
  },
});
