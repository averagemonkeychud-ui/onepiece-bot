#!/bin/bash
# Run this on your Oracle Cloud instance after SSH'ing in

# 1. Update & install Python + pip + git
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git

# 2. Install PM2 (process manager — auto-restarts on crash & op restart)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2

# 3. Clone your bot
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git ~/onepiece-bot
cd ~/onepiece-bot

# 4. Install Python deps
python3 -m venv venv
source venv/bin/activate
pip install discord.py

# 5. Set env vars (edit these!)
echo "export DISCORD_TOKEN='your_token_here'" >> ~/.bashrc
echo "export BOT_OWNER_ID='your_discord_id_here'" >> ~/.bashrc
source ~/.bashrc

# 6. Start with PM2 (auto-restarts on crash + picks up op restart)
pm2 start ecosystem.config.js
pm2 save
pm2 startup  # makes it survive VM reboots

echo ""
echo "=== DONE ==="
echo "op restart will now auto-restart the bot after updates"
echo "To update: git pull, then pm2 restart onepiece-bot"
