module.exports = {
  apps: [{
    name: "onepiece-bot",
    script: "bot.py",
    interpreter: "python3",
    autorestart: true,
    restart_delay: 3000,
    max_restarts: 10,
    env: {
      DISCORD_TOKEN: "",
      BOT_OWNER_ID: ""
    }
  }]
}
