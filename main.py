from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread
import os
from datetime import datetime, timezone
import csv
import io

# === KEEP ALIVE ===
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    Thread(target=run_web).start()

# === DISCORD BOT SETUP ===
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === CONFIGURATION ===
TARGET_CHANNEL_ID = 1379011966399545395
VOTER_ROLE_ID = 1379046751465373768
NODE_STAKER_ROLE_ID = 1379051411282722856
ADMIN_ROLE_ID = 1167708126716366939
GOON_DESIGNER_ROLE_ID = 1379372046886240278
EMOJI = "🔥"

# === DATA TRACKING ===
submitted_users = set()
tracked_messages = {}

# === SCORING FUNCTION ===
def calculate_weighted_score(user_id, guild):
    member = guild.get_member(user_id)
    return 3 if member and any(role.id == NODE_STAKER_ROLE_ID for role in member.roles) else 1

# === STARTUP MESSAGE SCAN ===
async def scan_and_react_on_startup():
    await bot.wait_until_ready()
    guild = bot.guilds[0]
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    async for message in channel.history(limit=100):
        if message.author.bot or message.id in tracked_messages:
            continue
        is_admin = any(role.id == ADMIN_ROLE_ID for role in message.author.roles)
        image_present = bool(message.attachments)
        text_present = bool(message.content.strip())
        if is_admin or (image_present and not text_present):
            try:
                await message.add_reaction(EMOJI)
                tracked_messages[message.id] = {
                    "author": str(message.author),
                    "link": message.jump_url,
                    "score": 0
                }
                submitted_users.add(message.author.id)
                role = guild.get_role(GOON_DESIGNER_ROLE_ID)
                if role and role not in message.author.roles:
                    await message.author.add_roles(role)
            except Exception as e:
                print(f"Startup error on message {message.id}: {e}")

# === BOT READY ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    await scan_and_react_on_startup()
    fetch_reactions.start()
    post_daily_leaderboard.start()

# === SUBMISSION HANDLER ===
@bot.event
async def on_message(message):
    if message.channel.id != TARGET_CHANNEL_ID or message.author.bot:
        return

    text_present = bool(message.content.strip())
    image_present = bool(message.attachments)
    is_admin = any(role.id == ADMIN_ROLE_ID for role in message.author.roles)

    try:
        for msg_id, data in list(tracked_messages.items()):
            if data["author"] == str(message.author):
                try:
                    prev = await message.channel.fetch_message(msg_id)
                    await prev.delete()
                except:
                    pass
                del tracked_messages[msg_id]
                submitted_users.discard(message.author.id)

        if is_admin or (image_present and not text_present):
            await message.add_reaction(EMOJI)
            tracked_messages[message.id] = {
                "author": str(message.author),
                "link": message.jump_url,
                "score": 0
            }
            submitted_users.add(message.author.id)
            role = message.guild.get_role(GOON_DESIGNER_ROLE_ID)
            if role and role not in message.author.roles:
                await message.author.add_roles(role)
        else:
            await message.delete()
            note = await message.channel.send(f"{message.author.mention} image-only posts are allowed.")
            await note.delete(delay=5)

    except Exception as e:
        print(f"Error in on_message: {e}")

# === EDIT PROTECTION ===
@bot.event
async def on_message_edit(before, after):
    if after.channel.id == TARGET_CHANNEL_ID and not after.author.bot:
        if not any(role.id == ADMIN_ROLE_ID for role in after.author.roles):
            try:
                await after.delete()
            except:
                pass

# === REACTION ROLE MANAGEMENT ===
@bot.event
async def on_raw_reaction_add(payload):
    if payload.channel_id == TARGET_CHANNEL_ID and str(payload.emoji.name) == EMOJI:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member and not member.bot:
            role = guild.get_role(VOTER_ROLE_ID)
            if role and role not in member.roles:
                await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id == TARGET_CHANNEL_ID and str(payload.emoji.name) == EMOJI:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member and not member.bot:
            channel = guild.get_channel(payload.channel_id)
            role = guild.get_role(VOTER_ROLE_ID)
            still_voting = False
            async for msg in channel.history(limit=100):
                message = await channel.fetch_message(msg.id)
                for react in message.reactions:
                    if str(react.emoji) == EMOJI:
                        async for user in react.users():
                            if user.id == payload.user_id:
                                still_voting = True
                                break
                if still_voting:
                    break
            if not still_voting and role:
                await member.remove_roles(role)

# === SCORING LOOP ===
@tasks.loop(minutes=2)
async def fetch_reactions():
    try:
        channel = bot.get_channel(TARGET_CHANNEL_ID)
        for msg_id in list(tracked_messages.keys()):
            try:
                message = await channel.fetch_message(msg_id)
                total = 0
                for react in message.reactions:
                    if str(react.emoji) == EMOJI:
                        async for user in react.users():
                            if not user.bot:
                                total += calculate_weighted_score(user.id, message.guild)
                tracked_messages[msg_id]["score"] = total
            except:
                del tracked_messages[msg_id]
    except Exception as e:
        print(f"Reaction fetch error: {e}")

# === DAILY LEADERBOARD ===
@tasks.loop(hours=24)
async def post_daily_leaderboard():
    try:
        channel = bot.get_channel(TARGET_CHANNEL_ID)
        if not tracked_messages:
            return
        sorted_data = sorted(tracked_messages.items(), key=lambda x: x[1]["score"], reverse=True)
        embed = discord.Embed(
            title="🏆 Daily Leaderboard",
            description=datetime.now(timezone.utc).strftime("%B %d, %Y"),
            color=0xFF9900
        )
        for i, (mid, data) in enumerate(sorted_data[:10], 1):
            embed.add_field(
                name=f"{i}. {data['author']}",
                value=f"🔥 {data['score']} points\n[Post]({data['link']})",
                inline=False
            )
        await channel.send(embed=embed)
        tracked_messages.clear()
        submitted_users.clear()
    except Exception as e:
        print(f"Leaderboard error: {e}")

# === COMMANDS ===
@bot.command(name="stats")
async def stats(ctx):
    if not tracked_messages:
        await ctx.send("No data yet.")
        return
    total = len(tracked_messages)
    avg = sum(d["score"] for d in tracked_messages.values()) / total
    top = max(d["score"] for d in tracked_messages.values())
    embed = discord.Embed(title="📊 Stats", color=0x00BFFF)
    embed.add_field(name="Submissions", value=total)
    embed.add_field(name="Users", value=len(submitted_users))
    embed.add_field(name="Avg Score", value=f"{avg:.1f}")
    embed.add_field(name="Top Score", value=top)
    await ctx.send(embed=embed)

@bot.command(name="leaderboard")
async def leaderboard(ctx):
    await post_daily_leaderboard()

@bot.command(name="export_csv")
async def export_csv(ctx):
    if not tracked_messages:
        await ctx.send("Nothing to export.")
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Author", "Score", "Link"])
    for d in tracked_messages.values():
        writer.writerow([d["author"], d["score"], d["link"]])
    buffer.seek(0)
    file = discord.File(io.BytesIO(buffer.getvalue().encode()), filename="leaderboard.csv")
    await ctx.send("📎 Exported CSV:", file=file)

# === RUN BOT ===
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
