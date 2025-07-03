#!/usr/bin/env python3
import subprocess
import sys
import os

def setup_dependencies():
    """
    Ensures all necessary system and Python dependencies are installed.
    This function is designed for Debian 12 and similar systems.
    """
    # This setup is intended for Debian-based systems like Debian 12 or Ubuntu.
    if sys.platform != "linux" or not os.path.exists("/etc/debian_version"):
        print("Warning: Automatic dependency installation is designed for Debian-based Linux.")
        print("Please ensure you have 'python3', 'ffmpeg', and 'pip' installed manually.")
        return

    print("Debian-based system detected. Setting up dependencies...")

    # 1. Install system packages using APT
    # We need python3, ffmpeg for audio processing, and python3-pip to manage Python packages.
    apt_packages = ["python3", "ffmpeg", "python3-pip"]
    try:
        print("Updating apt package information...")
        # Using capture_output to keep the startup process clean
        subprocess.run(["apt", "update", "-y"], check=True, capture_output=True, text=True)

        print(f"Installing system packages with apt: {', '.join(apt_packages)}...")
        # DEBIAN_FRONTEND=noninteractive prevents apt from prompting for input during installation.
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        subprocess.run(["apt", "install", "-y"] + apt_packages, env=env, check=True, capture_output=True, text=True)
        print("System dependencies are ready.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error during apt setup: {e.stderr}")
        print("Please try running 'sudo apt update && sudo apt install python3 ffmpeg python3-pip' manually.")
        sys.exit(1)

    # 2. Install Python packages using PIP
    # We use pip to get the latest versions, which is critical for yt-dlp and discord.py.
    # The versions in apt repositories can be too old to work correctly.
    pip_packages = ["discord.py", "yt-dlp", "PyNaCl", "google-api-python-client"]
    try:
        print(f"Installing Python packages with pip: {', '.join(pip_packages)}...")
        # We add --break-system-packages to bypass the PEP 668 error on modern Debian systems.
        # This allows the script to manage its own dependencies in this environment.
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade"] + pip_packages + ["--break-system-packages"])
        print("Python dependencies are ready.")
    except subprocess.CalledProcessError as e:
        print(f"Error during pip installation: {e}")
        sys.exit(1)

# Run the complete dependency setup when the bot starts
setup_dependencies()

import discord
from discord.ext import commands
import yt_dlp
import asyncio
from googleapiclient.discovery import build

# Your Discord bot token should be set as an environment variable for security.
# For example, in your terminal: export DISCORD_TOKEN="YOUR_REAL_BOT_TOKEN"
TOKEN = "DISCORD_TOKEN_HERE"
YOUTUBE_API_KEY = "API_KEY_HERE"  # Set this using the set_api_key command


if TOKEN is None:
    print("Error: DISCORD_TOKEN environment variable not set.")
    print("Please set the environment variable with your Discord bot token.")
    sys.exit(1)

song_queues = {}
stop_flags = {}
search_results = {}


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="?", intents=intents)

ytdl_format_options = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",  # bind to ipv4 since ipv6 addresses cause issues sometimes
    "cookiefile": "youtube_cookie.txt" if os.path.exists("youtube_cookie.txt") else None,
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "320",
    }],
}

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream)
        )

        if "entries" in data:
            # it's a playlist
            return [
                cls(discord.FFmpegPCMAudio(entry["url"], **ffmpeg_options), data=entry)
                for entry in data["entries"]
            ]
        else:
            # it's a single song
            filename = data["url"] if stream else ytdl.prepare_filename(data)
            return [cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)]

async def play_next(ctx):
    server_id = ctx.guild.id
    if server_id in stop_flags and stop_flags[server_id]:
        stop_flags[server_id] = False
        return
    if server_id in song_queues and song_queues[server_id]:
        player = song_queues[server_id].pop(0)
        ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        await ctx.send(f"**Now playing:** {player.title}")
    else:
        await ctx.send("Queue is empty.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.command(name="join", help="Tells the bot to join the voice channel")
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send(f"{ctx.message.author.name} is not connected to a voice channel")
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()


@bot.command(name="leave", help="To make the bot leave the voice channel")
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")



@bot.command(name="search", help="Searches for a song on YouTube")
async def search(ctx, *, query):
    if YOUTUBE_API_KEY is None:
        await ctx.send("YouTube API key is not set. Please ask the bot owner to set it.")
        return

    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        search_response = (
            youtube.search()
            .list(q=query, part="snippet", maxResults=10, type="video")
            .execute()
        )

        videos = []
        for search_result in search_response.get("items", []):
            videos.append(
                (
                    search_result["snippet"]["title"],
                    search_result["id"]["videoId"],
                )
            )

        if not videos:
            await ctx.send("No songs found for that query.")
            return

        server_id = ctx.guild.id
        search_results[server_id] = videos

        response = "**Search Results:**\n"
        for i, (title, video_id) in enumerate(videos):
            response += f"{i+1}. {title}\n"

        await ctx.send(response)

    except Exception as e:
        await ctx.send(f"An error occurred during search: {e}")


@bot.command(name="play", help="Adds a song or playlist to the queue")
async def play(ctx, *, query):
    server_id = ctx.guild.id
    if server_id not in song_queues:
        song_queues[server_id] = []
        stop_flags[server_id] = False

    try:
        # Check if the query is a number, to play from search results
        if query.isdigit():
            if server_id in search_results and 1 <= int(query) <= len(search_results[server_id]):
                video_id = search_results[server_id][int(query) - 1][1]
                query = f"https://www.youtube.com/watch?v={video_id}"
            else:
                await ctx.send("Invalid number from search results.")
                return

        async with ctx.typing():
            players = await YTDLSource.from_url(query, loop=bot.loop)
            if not players:
                await ctx.send("Could not find any songs with that query.")
                return

            for player in players:
                song_queues[server_id].append(player)

            if len(players) > 1:
                await ctx.send(f"**Added {len(players)} songs to the queue.**")
            else:
                await ctx.send(f"**Added to queue:** {players[0].title}")

        if not ctx.voice_client.is_playing():
            await play_next(ctx)

    except Exception as e:
        await ctx.send(f"An error occurred: {e}")



@bot.command(name="pause", help="This command pauses the song")
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
    else:
        await ctx.send("The bot is not playing anything at the moment.")


@bot.command(name="resume", help="Resumes the song")
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
    else:
        await ctx.send(
            "The bot was not playing anything before this. Use play_url command"
        )






@bot.command(name="queue", help="Displays the song queue")
async def queue(ctx):
    server_id = ctx.guild.id
    if server_id in song_queues and song_queues[server_id]:
        queue_list = "\n".join([f"{i+1}. {player.title}" for i, player in enumerate(song_queues[server_id])])
        await ctx.send(f"**Current Queue:**\n{queue_list}")
    else:
        await ctx.send("The queue is empty.")

@bot.command(name="skip", help="Skips the current song")
async def skip(ctx):
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped the song.")
    else:
        await ctx.send("Not playing anything.")

@bot.command(name="clear", help="Clears the song queue")
async def clear(ctx):
    server_id = ctx.guild.id
    if server_id in song_queues:
        song_queues[server_id] = []
    await ctx.send("Queue has been cleared.")

@bot.command(name="stop", help="Stops the song and clears the queue")
async def stop(ctx):
    server_id = ctx.guild.id
    if server_id in song_queues:
        song_queues[server_id] = []
    if ctx.voice_client.is_playing():
        stop_flags[server_id] = True
        ctx.voice_client.stop()
    await ctx.send("Music stopped and queue cleared.")


if __name__ == "__main__":
    # In a real-world scenario, you would want to get the bot owner's ID
    # in a more secure way, such as from a configuration file or environment variable.
    # For this example, we'll just use a placeholder.
    bot_owner_id = "bot_owner_id"  # Replace with your actual Discord user ID

    @bot.command(name="set_token", help="Sets the bot's token (owner only)")
    @commands.is_owner()
    async def set_token(ctx, *, new_token):
        global TOKEN
        
        # Create a new client to test the token
        test_client = discord.Client(intents=intents)
        
        try:
            # Try to login with the new token
            await test_client.login(new_token)
            await test_client.close()
            
            # If login is successful, update the token
            TOKEN = new_token
            await ctx.send("Token has been updated. Please restart the bot for the change to take effect.")
        except discord.errors.LoginFailure:
            await ctx.send("The provided token is invalid.")

    @set_token.error
    async def set_token_error(ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("You are not the owner of this bot.")

    @bot.command(name="set_yt_cookie", help="Sets the YouTube cookie (owner only)")
    @commands.is_owner()
    async def set_yt_cookie(ctx, *, cookie_content: str):
        try:
            with open("youtube_cookie.txt", "w") as f:
                f.write(cookie_content)
            await ctx.send("YouTube cookie has been set. Please restart the bot for the change to take effect.")
        except Exception as e:
            await ctx.send(f"An error occurred while setting the cookie: {e}")

    @set_yt_cookie.error
    async def set_yt_cookie_error(ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("You are not the owner of this bot.")

    @bot.command(name="set_api_key", help="Sets the YouTube API key (owner only)")
    @commands.is_owner()
    async def set_api_key(ctx, *, api_key):
        global YOUTUBE_API_KEY
        YOUTUBE_API_KEY = api_key
        await ctx.send("YouTube API key has been set.")

    @set_api_key.error
    async def set_api_key_error(ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("You are not the owner of this bot.")

    bot.run(TOKEN)
