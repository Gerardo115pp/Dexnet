from os import getenv
from bot import DiscordBot

DISCORD_TOKEN = getenv("BOT_TOKEN")
assert DISCORD_TOKEN, "BOT_TOKEN not set"

bot = DiscordBot(DISCORD_TOKEN)
bot.run()