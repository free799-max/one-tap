import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // vendor 分包：将大型依赖拆出主 chunk，利用浏览器缓存
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          if (id.includes("@xterm")) return "xterm";
          if (
            id.includes("@codemirror") ||
            id.includes("@uiw") ||
            id.includes("@lezer")
          ) {
            return "codemirror";
          }
          if (
            id.includes("react-markdown") ||
            /(remark-|rehype-|micromark|mdast|hast|unified|vfile)/.test(id)
          ) {
            return "markdown";
          }
          if (
            /node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id) ||
            id.includes("react-router")
          ) {
            return "react";
          }
          if (id.includes("@tanstack") || id.includes("axios")) return "query";
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5000,
    proxy: {
      "/api": {
        target: "http://localhost:5001",
        // 保留浏览器的 Host，后端据此生成服务跳转链接的内网地址
        changeOrigin: false,
        ws: true,
      },
    },
  },
});
