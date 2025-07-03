#!/usr/bin/env python3
import subprocess
import sys
import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp
from googleapiclient.discovery import build

def setup_dependencies():
    """
    Ensures all necessary system and Python dependencies are installed.
    This function is designed for Debian 12 and similar systems.
    """
    if sys.platform != "linux" or not os.path.exists("/etc/debian_version"):
        print("Warning: Automatic dependency installation is designed for Debian-based Linux.")
        print("Please ensure you have 'python3', 'ffmpeg', and 'pip' installed manually.")
        return

    print("Debian-based system detected. Setting up dependencies...")

    apt_packages = ["python3", "ffmpeg", "python3-pip"]
    try:
        print("Updating apt package information...")
        subprocess.run(["apt", "update", "-y"], check=True, capture_output=True, text=True)
        print(f"Installing system packages with apt: {', '.join(apt_packages)}...")
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        subprocess.run(["apt", "install", "-y"] + apt_packages, env=env, check=True, capture_output=True, text=True)
        print("System dependencies are ready.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error during apt setup: {e.stderr}")
        print("Please try running 'sudo apt update && sudo apt install python3 ffmpeg python3-pip' manually.")
        sys.exit(1)

    pip_packages = ["discord.py", "yt-dlp", "PyNaCl", "google-api-python-client"]
    try:
        print(f"Installing Python packages with pip: {', '.join(pip_packages)}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade"] + pip_packages + ["--break-system-packages"])
        print("Python dependencies are ready.")
    except subprocess.CalledProcessError as e:
        print(f"Error during pip installation: {e}")
        sys.exit(1)

setup_dependencies()

TOKEN = os.environ.get("DISCORD_TOKEN", "DISCORD_TOKEN_HERE")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "API_KEY_HERE")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "BOT_OWNER_ID_HERE"))

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
    "source_address": "0.0.0.0",
    "cookiefile": "youtube_cookie.txt" if os.path.exists("youtube_cookie.txt") else None,
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "320",
    }],
}

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -filter:a 'volume=1.0'",
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
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if "entries" in data:
            return [cls(discord.FFmpegPCMAudio(entry["url"], **ffmpeg_options), data=entry) for entry in data["entries"]]
        else:
            return [cls(discord.FFmpegPCMAudio(data["url"], **ffmpeg_options), data=data)]

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queues = {}
        self.search_results = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Logged in as {self.bot.user} (ID: {self.bot.user.id})")
        print("------")

    async def get_queue(self, ctx):
        if ctx.guild.id not in self.song_queues:
            self.song_queues[ctx.guild.id] = asyncio.Queue()
        return self.song_queues[ctx.guild.id]

    @commands.command(name="join")
    async def join(self, ctx):
        if not ctx.author.voice:
            return await ctx.send("You are not connected to a voice channel.")
        if ctx.voice_client:
            return await ctx.voice_client.move_to(ctx.author.voice.channel)
        await ctx.author.voice.channel.connect()

    @commands.command(name="leave")
    async def leave(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()

    @commands.command(name="search")
    async def search(self, ctx, *, query):
        if not YOUTUBE_API_KEY:
            return await ctx.send("YouTube API key is not set.")
        try:
            youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
            search_response = youtube.search().list(q=query, part="snippet", maxResults=10, type="video").execute()
            videos = [(item["snippet"]["title"], item["id"]["videoId"]) for item in search_response.get("items", [])]
            if not videos:
                return await ctx.send("No songs found.")
            self.search_results[ctx.guild.id] = videos
            response = "**Search Results:**\n" + "\n".join(f"{i+1}. {title}" for i, (title, _) in enumerate(videos))
            await ctx.send(response)
        except Exception as e:
            await ctx.send(f"An error occurred during search: {e}")

    @commands.command(name="play")
    async def play(self, ctx, *, query):
        queue = await self.get_queue(ctx)
        
        try:
            if query.isdigit() and ctx.guild.id in self.search_results:
                video_id = self.search_results[ctx.guild.id][int(query) - 1][1]
                url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                url = query

            async with ctx.typing():
                players = await YTDLSource.from_url(url, loop=self.bot.loop)
                for player in players:
                    await queue.put(player)
                
                if len(players) > 1:
                    await ctx.send(f"**Added {len(players)} songs to the queue.**")
                else:
                    await ctx.send(f"**Added to queue:** {players[0].title}")

            if not ctx.voice_client.is_playing():
                await self.play_next(ctx)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    async def play_next(self, ctx):
        queue = await self.get_queue(ctx)
        if not queue.empty() and ctx.voice_client:
            player = await queue.get()
            ctx.voice_client.play(player, after=lambda e: self.bot.loop.create_task(self.play_next(ctx)))
            await ctx.send(f"**Now playing:** {player.title}")

    @commands.command(name="volume")
    async def volume(self, ctx, volume: int):
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = volume / 100
            await ctx.send(f"Volume set to {volume}%")

    @commands.command(name="nowplaying")
    async def nowplaying(self, ctx):
        if ctx.voice_client and ctx.voice_client.source:
            await ctx.send(f"**Now playing:** {ctx.voice_client.source.title}")
        else:
            await ctx.send("Not playing anything.")

    @commands.command(name="queue")
    async def queue_info(self, ctx):
        queue = await self.get_queue(ctx)
        if not queue.empty():
            queue_list = "\n".join(f"{i+1}. {player.title}" for i, player in enumerate(list(queue._queue)))
            await ctx.send(f"**Current Queue:**\n{queue_list}")
        else:
            await ctx.send("The queue is empty.")

    @commands.command(name="skip")
    async def skip(self, ctx):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("Skipped the song.")

    @commands.command(name="stop")
    async def stop(self, ctx):
        queue = await self.get_queue(ctx)
        while not queue.empty():
            await queue.get()
        if ctx.voice_client:
            ctx.voice_client.stop()
        await ctx.send("Music stopped and queue cleared.")

    @commands.command(name="set_token")
    @commands.is_owner()
    async def set_token(self, ctx, *, new_token):
        global TOKEN
        TOKEN = new_token
        await ctx.send("Token updated. Please restart the bot.")

    @commands.command(name="set_api_key")
    @commands.is_owner()
    async def set_api_key(self, ctx, *, api_key):
        global YOUTUBE_API_KEY
        YOUTUBE_API_KEY = api_key
        await ctx.send("YouTube API key updated.")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="?", intents=intents, owner_id=BOT_OWNER_ID)

async def main():
    async with bot:
        await bot.add_cog(MusicCog(bot))
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
