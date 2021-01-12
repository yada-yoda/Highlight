import discord
from discord.ext import commands, menus

import asyncio
import asyncpg
import typing
import re
import datetime
import dateparser
import humanize
import logging

class TimeConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            if not arg.startswith("in") and not arg.startswith("at"):
                arg = f"in {arg}"
            time = dateparser.parse(arg, settings={"TIMEZONE": "UTC"})
        except:
            raise commands.BadArgument("Failed to parse time")
        if not time:
            raise commands.BadArgument("Failed to parse time")
        return time

log = logging.getLogger("cogs.highlight")

class Highlight(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def cog_check(self, ctx):
        return ctx.guild

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild:
            return
        if message.author.bot:
            return

        sent = []
        for cached_word in self.bot.cached_words:
            if self.word_in_message(cached_word, message.content.lower()):
                query = """SELECT *
                           FROM words
                           WHERE words.guild_id=$1 AND words.word=$2;
                        """
                words = await self.bot.db.fetch(query, message.guild.id, cached_word)
                if not words:
                    continue

                # Somehow the guild isn't chunked
                if not message.guild.chunked:
                    log.warning("Guild ID %s is somehow not chunked. Chunking it now.", message.guild.id)
                    await message.guild.chunk(cache=True)

                for word in words:
                    if word["user_id"] not in sent:
                        self.bot.loop.create_task(self.send_highlight(message, word))
                        sent.append(word["user_id"])

    async def send_highlight(self, message, word):
        member = message.guild.get_member(word["user_id"])
        if not member:
            log.info("Received a highlight for user ID %s (guild ID %s) but member is None", word["user_id"], word["guild_id"])
            return

        # Get user settings
        query = """SELECT *
                   FROM settings
                   WHERE settings.user_id=$1;
                """
        settings = await self.bot.db.fetchrow(query, member.id)
        if not settings:
            settings = {"user_id": member.id, "disabled": False, "timezone": 0, "blocked_users": [], "blocked_channels": []}
        timezone = settings["timezone"]
        word = word["word"]

        # Run various checks
        if member.id == message.author.id or settings["disabled"]:
            return
        if member.id not in [member.id for member in message.channel.members]:
            return
        if message.channel.id in settings["blocked_channels"] or message.author.id in settings["blocked_users"]:
            return

        utc = ""
        if timezone == 0:
            utc = " UTC"

        # Base embed
        description = f"In {message.channel.mention} for `{discord.utils.escape_markdown(message.guild.name)}` you were highlighted with the word **{discord.utils.escape_markdown(word)}**\n\n"
        em = discord.Embed(description=description, timestamp=message.created_at, color=discord.Color.blurple())
        em.set_author(name=message.author.display_name, icon_url=message.author.avatar_url)
        em.add_field(name="Jump", value=f"[Jump!]({message.jump_url})")
        em.set_footer(text="Triggered")

        try:
            history = await message.channel.history(limit=3, before=message).flatten()
            messages = []
            for ms in reversed(history):
                content = f"{ms.content[:50]}{'...' if len(ms.content) > 50 else ''}"
                time = (ms.created_at+datetime.timedelta(hours=timezone)).strftime("%H:%M:%S")
                messages.append(f"`{time}{utc}` {discord.utils.escape_markdown(str(ms.author))}: {discord.utils.escape_markdown(content)}")
            em.description += "\n".join(messages)
        except discord.HTTPException:
            pass

        # Add trigger message to the embed
        span = re.search(word, message.content.lower()).span()
        content = discord.utils.escape_markdown(message.content[:span[0]])
        content += f"**{discord.utils.escape_markdown(word)}**"
        content += discord.utils.escape_markdown(message.content[span[1]:])

        content = f"{content[:50]}{'...' if len(content) > 50 else ''}"
        time = (message.created_at+datetime.timedelta(hours=timezone)).strftime("%H:%M:%S")
        em.description += f"\n> `{time}{utc}` {discord.utils.escape_markdown(str(message.author))}: {content}"

        # Check for new messages to the embed
        try:
            ms = await self.bot.wait_for("message", check=lambda ms: ms.channel == message.channel, timeout=10)
            if ms.author.id == member.id:
                return

            content = f"{ms.content[:50]}{'...' if len(ms.content) > 50 else ''}"
            time = (ms.created_at+datetime.timedelta(hours=timezone)).strftime("%H:%M:%S")
            em.description += f"\n`{time}{utc}` {discord.utils.escape_markdown(str(ms.author))}: {discord.utils.escape_markdown(content)}"
        except asyncio.TimeoutError:
            pass

        try:
            await member.send(embed=em)
        except discord.Forbidden:
            log.warning("Forbidden to send highlight message to user ID %s", member.id)

    def word_in_message(self, word, message):
        # Get the word in the message
        match = re.search(word, message)

        # Return False if the word is not in the message
        if not match:
            return False

        span = match.span()

        start = span[0]-1
        end = span[1]

        if start >= 0:
            # If the charecter before the word is not a space, return False
            if message[start] != " ":
                return False

        return True

    async def can_dm(self, user):
        try:
            await user.send()
        except discord.HTTPException as exc:
            if exc.code == 50006:
                return True
            elif exc.code == 50007:
                return False
            else:
                raise

    @commands.command(name="add", description="Add a highlight word")
    async def add(self, ctx, *, word):
        word = word.lower()
        can_dm = await self.can_dm(ctx.author)

        if not can_dm:
            await ctx.send(":x: You need to enable DMs", delete_after=5)
        elif f"<@!{self.bot.user.id}>" in word:
            await ctx.send(":x: Your highlight word can't mention me", delete_after=5)
        elif len(word) < 2:
            await ctx.send(":x: Your word must be at least 2 characters", delete_after=5)
        elif len(word) > 20:
            await ctx.send(":x: Your word cannot be bigger than 20 characters", delete_after=5)

        try:
            query = """INSERT INTO words (user_id, guild_id, word)
                       VALUES ($1, $2, $3);
                    """
            await self.bot.db.execute(query, ctx.author.id, ctx.guild.id, word)

            if word not in self.bot.cached_words:
                self.bot.cached_words.append(word)
            await ctx.send(f":white_check_mark: Added `{word}` to your highlight list", delete_after=5)
        except asyncpg.UniqueViolationError:
            await ctx.send(":x: You already have this word", delete_after=5)

        try:
           await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="remove", description="Remove a highlight word")
    async def remove(self, ctx, *, word):
        query = """DELETE FROM words
                   WHERE words.user_id=$1 AND words.guild_id=$2 AND words.word=$3;
                """
        result = await self.bot.db.execute(query, ctx.author.id, ctx.guild.id, word)

        if result == "DELETE 0":
            await ctx.send(":x: This word is not registered", delete_after=5)
        else:
            await ctx.send(f":white_check_mark: Removed `{word}` from your highlight list", delete_after=5)

        try:
           await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="clear", description="Clear your highlight list")
    async def clear(self, ctx):
        query = """DELETE FROM words
                    WHERE words.user_id=$1 AND words.guild_id=$2;
                """
        result = await self.bot.db.execute(query, ctx.author.id, ctx.guild.id)

        await ctx.send(f":white_check_mark: Your highlight list has been cleared", delete_after=5)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="import", description="Import your words from another server", usage="<server id>", aliases=["transfer"])
    async def transfer(self, ctx, guild_id: int):
        query = """SELECT *
                   FROM words
                   WHERE words.user_id=$1 AND (words.guild_id=$2 OR words.guild_id=$3);
                """
        words = await self.bot.db.fetch(query, ctx.author.id, guild_id, ctx.guild.id)
        words = [dict(word) for word in words]

        to_transfer = []
        for word in words:
            if word["guild_id"] == guild_id and word["word"] not in [word["word"] for word in words if word["guild_id"] == ctx.guild.id]:
                word["guild_id"] = ctx.guild.id
                to_transfer.append(word)

        if to_transfer:
            query = """INSERT INTO words (user_id, guild_id, word)
                       SELECT x.user_id, x.guild_id, x.word
                       FROM jsonb_to_recordset($1::jsonb) AS
                       x(user_id BIGINT, guild_id BIGINT, word TEXT);
                    """

            await self.bot.db.execute(query, to_transfer)
            await ctx.send(":white_check_mark: Your highlight list has been imported", delete_after=5)
        else:
            await ctx.send(":x: You have no words to transfer from this server", delete_after=5)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="show", description="View your words for the current server", aliases=["words", "list"])
    async def show(self, ctx):
        query = """SELECT * FROM words
                   WHERE words.user_id=$1 AND words.guild_id=$2;
                """
        rows = await self.bot.db.fetch(query, ctx.author.id, ctx.guild.id)

        if not rows:
            await ctx.send("You have no words for this server", delete_after=10)
        else:
            em = discord.Embed(title="Highlight Words", color=discord.Color.blurple())
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar_url)

            em.description = ""
            for row in rows:
                em.description += f"\n{row['word']}"

            await ctx.send(embed=em, delete_after=10)

        try:
           await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="block", description="Block a user or channel", usage="<user or channel>", aliases=["ignore", "mute"])
    async def block(self, ctx, *, user: typing.Union[discord.User, discord.TextChannel]):
        query = """SELECT *
                   FROM settings
                   WHERE settings.user_id=$1;
                """
        settings = await self.bot.db.fetchrow(query, ctx.author.id)

        if isinstance(user, discord.User):
            if settings:
                if user.id in settings["blocked_users"]:
                    await ctx.send(":x: This user is already blocked", delete_after=5)
                else:
                    settings["blocked_users"].append(user.id)
                    query = """UPDATE settings
                               SET blocked_users=$1
                               WHERE settings.user_id=$2;
                            """
                    await self.bot.db.execute(query, settings["blocked_users"], ctx.author.id)
                    await ctx.send(f":no_entry_sign: Blocked `{user.display_name}`", delete_after=5)
            else:
                query = """INSERT INTO settings (user_id, disabled, timezone, blocked_users, blocked_channels)
                           VALUES ($1, $2, $3, $4, $5);
                        """
                await self.bot.db.execute(query, ctx.author.id, False, 0, [user.id], [])
        else:
            if settings:
                if user.id in settings["blocked_channels"]:
                    await ctx.send(":x: This channel is already blocked", delete_after=5)
                else:
                    settings["blocked_channels"].append(user.id)
                    query = """UPDATE settings
                               SET blocked_channels=$1
                               WHERE settings.user_id=$2;
                            """
                    await self.bot.db.execute(query, settings["blocked_channels"], ctx.author.id)
                    await ctx.send(f":no_entry_sign: Blocked {user.mention}", delete_after=5)
            else:
                query = """INSERT INTO settings (user_id, disabled, timezone, blocked_users, blocked_channels)
                           VALUES ($1, $2, $3, $4, $5);
                        """
                await self.bot.db.execute(query, ctx.author.id, False, 0, [], [user.id])

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="unblock", description="Unblock a user or channel", usage="<user or channel>", aliases=["unmute"])
    async def unblock(self, ctx, *, user: typing.Union[discord.User, discord.TextChannel]):
        query = """SELECT *
                   FROM settings
                   WHERE settings.user_id=$1;
                """
        settings = await self.bot.db.fetchrow(query, ctx.author.id)

        if isinstance(user, discord.User):

            if settings:
                if user.id not in settings["blocked_users"]:
                    await ctx.send(":x: This user is not blocked", delete_after=5)
                else:
                    settings["blocked_users"].remove(user.id)
                    query = """UPDATE settings
                               SET blocked_users=$1
                               WHERE settings.user_id=$2;
                            """
                    await self.bot.db.execute(query, settings["blocked_users"], ctx.author.id)
                    await ctx.send(f":white_check_mark: Unblocked `{user.display_name}`", delete_after=5)
            else:
                await ctx.send(":x: This user is not blocked", delete_after=5)

        else:

            if settings:
                if user.id not in settings["blocked_channels"]:
                    await ctx.send(":x: This channel is not blocked", delete_after=5)
                else:
                    settings["blocked_channels"].remove(user.id)
                    query = """UPDATE settings
                               SET blocked_channels=$1
                               WHERE settings.user_id=$2;
                            """
                    await self.bot.db.execute(query, settings["blocked_channels"], ctx.author.id)
                    await ctx.send(f":white_check_mark: Unblocked {user.mention}", delete_after=5)
            else:
                await ctx.send(":x: This channel is not blocked")

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.group(name="blocked", description="View your blocked list", invoke_without_command=True)
    async def blocked(self, ctx):
        query = """SELECT *
                   FROM settings
                   WHERE settings.user_id=$1;
                """
        settings = await self.bot.db.fetchrow(query, ctx.author.id)

        if not settings or (not settings["blocked_channels"] and not settings["blocked_users"]):
            await ctx.send(":x: You have no channnels or users blocked", delete_after=5)
        else:
            em = discord.Embed(color=discord.Color.blurple())
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar_url)

            users = []
            for user_id in settings["blocked_users"]:
                user = self.bot.get_user(user_id)
                users.append(user.mention if user else f"User with ID of {user_id}")
            if users:
                em.add_field(name="Blocked Users", value="\n".join(users))

            channels = []
            for channel_id in settings["blocked_channels"]:
                channel = self.bot.get_channel(channel_id)
                channels.append(channel.mention if channel else f"Channel with ID of {channel_id}")
            if channels:
                em.add_field(name="Blocked Channels", value="\n".join(channels))

            await ctx.send(embed=em, delete_after=10)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @blocked.command(name="clear", description="Clear your blocked list")
    async def blocked_clear(self, ctx):
        query = """UPDATE settings
                   SET blocked_users=$1, blocked_channels=$2
                   WHERE settings.user_id=$3;
                """
        await self.bot.db.execute(query, [], [], ctx.author.id)

        await ctx.send(f":white_check_mark: Your blocked list has been cleared")

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="enable", description="Enable highlight")
    async def enable(self, ctx):
        await self.bot.get_cog("Timers").cancel_timer(ctx.author.id, "disable")

        query = """INSERT INTO settings (user_id, disabled, timezone, blocked_users, blocked_channels)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (user_id)
                   DO UPDATE SET disabled=$2;
                """
        await self.bot.db.execute(query, ctx.author.id, False, 0, [], [])

        await ctx.send(":white_check_mark: Highlight has been enabled", delete_after=5)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="disable", description="Disable highlight", aliases=["dnd"])
    async def disable(self, ctx, *, time: TimeConverter = None):
        await self.bot.get_cog("Timers").cancel_timer(ctx.author.id, "disable")

        query = """INSERT INTO settings (user_id, disabled, timezone, blocked_users, blocked_channels)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (user_id)
                   DO UPDATE SET disabled=$2;
                """
        await self.bot.db.execute(query, ctx.author.id, True, 0, [], [])

        if time:
            await self.bot.get_cog("Timers").create_timer(ctx.author.id, "disabled", time, {})

        await ctx.send(f":white_check_mark: Highlight has been disabled {f'`for {humanize.naturaldelta(time-datetime.datetime.utcnow())}`' if time else ''}", delete_after=5)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="timezone", description="Set your timezone")
    async def timezone(self, ctx, timezone: int = None):
        if timezone:
            query = """INSERT INTO settings (user_id, disabled, timezone, blocked_users, blocked_channels)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (user_id)
                       DO UPDATE SET timezone=$3;
                    """

            await self.bot.db.execute(query, ctx.author.id, False, timezone, [], [])
            await ctx.send(f":white_check_mark: Timezone set to `{timezone}`", delete_after=5)

        else:
            query = """SELECT *
                       FROM settings
                       WHERE settings.user_id=$1;
                    """
            settings = await self.bot.db.fetchrow(query, ctx.author.id)
            if settings:
                await ctx.send(f"Your current timezone is `{settings['timezone']}`", delete_after=10)
            else:
                await ctx.send(f"Your current timezone is `{0}`", delete_after=10)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_disabled_complete(self, timer):
        query = """INSERT INTO settings (user_id, disabled, timezone)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (user_id)
                   DO UPDATE SET disabled=$2;
                """
        await self.bot.db.execute(query, timer["user_id"], False, 0)

def setup(bot):
    bot.add_cog(Highlight(bot))
