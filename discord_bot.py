import os
import discord
from discord.ext import commands
from app.models import User, Score, DiscordLink
from app.extensions import db

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

import functools

# Flask app context wrapper
def with_app_context(func):
    @functools.wraps(func)
    async def wrapper(ctx, *args, **kwargs):
        from wsgi import app
        with app.app_context():
            return await func(ctx, *args, **kwargs)
    return wrapper

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
@with_app_context
async def link(ctx, *, osu_username: str):
    user = db.session.query(User).filter(User.username.ilike(osu_username)).first()
    if not user:
        await ctx.send(f"Could not find an osu! user named '{osu_username}' in the leaderboard.")
        return

    link = db.session.get(DiscordLink, str(ctx.author.id))
    if link:
        link.osu_user_id = user.id
    else:
        link = DiscordLink(discord_id=str(ctx.author.id), osu_user_id=user.id)
        db.session.add(link)
    db.session.commit()
    await ctx.send(f"Successfully linked your Discord account to osu! user: **{user.username}**!")

@bot.command()
@with_app_context
async def me(ctx):
    link = db.session.get(DiscordLink, str(ctx.author.id))
    if not link:
        await ctx.send("You haven't linked your osu! account yet. Use `!link <username>` first.")
        return

    user = link.user
    if not user:
        await ctx.send("Your linked osu! account could not be found.")
        return

    from sqlalchemy import func
    rank = None
    if user.total_pp is not None:
        rank = db.session.query(func.count(User.id)).filter(User.total_pp > user.total_pp).scalar() + 1
    
    embed = discord.Embed(title=f"Profile for {user.username}", color=discord.Color.blue())
    embed.set_thumbnail(url=f"https://a.ppy.sh/{user.id}")
    embed.add_field(name="Global Rank", value=f"#{rank}" if rank else "Unranked", inline=True)
    embed.add_field(name="Total PP", value=f"{user.total_pp:.0f}pp" if user.total_pp else "0pp", inline=True)
    embed.add_field(name="Accuracy", value=f"{user.total_accuracy:.2f}%" if user.total_accuracy else "N/A", inline=True)
    await ctx.send(embed=embed)

@bot.command()
@with_app_context
async def leaderboard(ctx, *, username: str = None):
    if username:
        user = db.session.query(User).filter(User.username.ilike(username)).first()
        if not user:
            await ctx.send(f"Could not find user '{username}'.")
            return
        
        from sqlalchemy import func
        rank = None
        if user.total_pp is not None:
            rank = db.session.query(func.count(User.id)).filter(User.total_pp > user.total_pp).scalar() + 1
        
        embed = discord.Embed(title=f"Leaderboard Position: {user.username}", color=discord.Color.gold())
        embed.set_thumbnail(url=f"https://a.ppy.sh/{user.id}")
        embed.description = f"**{user.username}** is currently ranked **#{rank}** globally with **{user.total_pp:.0f}pp**!"
        await ctx.send(embed=embed)
    else:
        top_users = db.session.query(User).filter(User.total_pp.isnot(None)).order_by(User.total_pp.desc()).limit(10).all()
        if not top_users:
            await ctx.send("The leaderboard is currently empty.")
            return

        desc = ""
        for i, u in enumerate(top_users, start=1):
            desc += f"**#{i}** {u.username} - {u.total_pp:.0f}pp\n"

        embed = discord.Embed(title="Global Leaderboard (Top 10)", description=desc, color=discord.Color.gold())
        await ctx.send(embed=embed)

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Missing DISCORD_TOKEN in environment")
        import sys
        sys.exit(1)
    bot.run(token)
