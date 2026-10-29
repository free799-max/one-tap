// 本机开发备选方案（pm2）：正式部署请使用 docker-compose（见 README.md）。
// 用法：在项目根目录执行 pm2 start ecosystem.config.js
module.exports = {
  apps: [
    {
      name: "onetap-backend",
      cwd: __dirname,
      script: "./start-backend.sh",
      autorestart: true,
      max_restarts: 5,
      min_uptime: "10s",
    },
    {
      name: "onetap-frontend",
      cwd: `${__dirname}/frontend`,
      // 依赖 PATH 中的 pnpm（请先安装 Node.js 20+ 与 pnpm）
      script: "pnpm",
      args: "run dev",
      autorestart: true,
      max_restarts: 5,
      min_uptime: "10s",
    },
  ],
};
