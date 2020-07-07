import discord
from discord.ext import commands

import json
import asyncpg
import logging

logger = logging.getLogger("discord")
logger.setLevel(logging.INFO)

logging.basicConfig(

    level = logging.INFO,
    format = "(%(asctime)s) %(levelname)s %(message)s",
    datefmt="%m/%d/%y - %H:%M:%S %Z" 
)

def get_prefix(client, msg):
    return commands.when_mentioned(client, msg)

class HighlightBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=get_prefix)

        with open("config.json", "r") as f:
            self.config = json.load(f)
        
        self.cogs_to_add = ["cogs.highlight"]

        self.loop.create_task(self.load_cogs())
        self.loop.create_task(self.prepare_bot())

    async def load_cogs(self):
        self.load_extension("jishaku")
        self.get_command("jishaku").hidden = True

        for cog in self.cogs_to_add:
            self.load_extension(cog)

    async def prepare_bot(self):
        self.db = await asyncpg.create_pool(self.config["sql"])
        
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS words(
                userid text,
                guildid text,
                word text
            )
        ''')

        self.cached_words = await self.db.fetch("SELECT word FROM words")
        
    async def on_ready(self):
        logging.info(f"Logged in as {self.user.name} - {self.user.id}")
    def run(self):
        super().run(self.config["token"])
        

bot = HighlightBot()
bot.run()