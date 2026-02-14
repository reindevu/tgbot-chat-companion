module.exports = {
  apps: [
    {
      name: "tgbot-chat-companion",
      cwd: "/path/to/folder",
      script: "src/bot.py",
      interpreter: "python3",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        NODE_ENV: "production"
      }
    }
  ]
};
