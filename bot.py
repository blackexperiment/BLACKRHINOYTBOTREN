# bot.py
import os
import asyncio
import tempfile
import shutil
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyromod import listen
from yt_dlp import YoutubeDL
from web import run_web
import aiohttp
from aiohttp import ClientTimeout

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ytbot")

# --- Environment / secrets (set these in Render dashboard) ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # required
# Optional: comma separated sudo users
SUDO_USERS_ENV = os.getenv("SUDO_USERS", "")  # e.g. "12345,67890"
PORT = int(os.getenv("PORT", 10000))

# Convert SUDO_USERS to list[int]
SUDO_USERS = []
if SUDO_USERS_ENV:
    for v in SUDO_USERS_ENV.split(","):
        try:
            uid = int(v.strip())
            if uid:
                SUDO_USERS.append(uid)
        except:
            pass

SUDO_USERS = list(set(SUDO_USERS + [OWNER_ID]))

# Default image for /start
DEFAULT_IMG = os.getenv("DEFAULT_IMG", "https://graph.org/file/5ed50675df0faf833efef-e102210eb72c1d5a17.jpg")

# Temp folder
TMP_DIR = tempfile.mkdtemp(prefix="ytbot_")
logger.info("Temp dir: %s", TMP_DIR)

# --- Helper: authorization ---
def is_authorized(user_id: int) -> bool:
    return user_id in SUDO_USERS

# --- Helper: send image by url or upload fallback (uses aiohttp) ---
async def send_photo_via_url_or_upload(bot, chat_id, url, caption=None, reply_markup=None, max_bytes=10_000_000):
    logger.info("Attempt send image url: %s", url)
    try:
        # Try direct first (pyrogram will fetch remote URL)
        await bot.send_photo(chat_id=chat_id, photo=url, caption=caption, reply_markup=reply_markup)
        return
    except Exception as e:
        logger.warning("Direct send failed, will fallback. err=%s", e)

    tmp_path = None
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                logger.info("Downloaded %s -> status %s", url, resp.status)
                if resp.status != 200:
                    logger.warning("Primary URL returned %s, trying default image", resp.status)
                    if url != DEFAULT_IMG:
                        await send_photo_via_url_or_upload(bot, chat_id, DEFAULT_IMG, caption=caption, reply_markup=reply_markup)
                    else:
                        await bot.send_message(chat_id, f"Image fetch failed (HTTP {resp.status})")
                    return

                ctype = (resp.headers.get("content-type") or "").lower()
                if not ctype.startswith("image"):
                    logger.warning("Not an image: %s", ctype)
                    await bot.send_message(chat_id, f"URL did not return an image (content-type: {ctype}).")
                    return

                tmp_path = os.path.join(TMP_DIR, "tmp_image")
                with open(tmp_path, "wb") as f:
                    data = await resp.read()
                    f.write(data)

                await bot.send_photo(chat_id=chat_id, photo=tmp_path, caption=caption, reply_markup=reply_markup)
                return
    except Exception as e:
        logger.exception("Error downloading image fallback: %s", e)
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

# --- YT Download helpers ---
def download_video_with_ydl(url: str, outdir: str, fmt: str = "best"):
    outtmpl = os.path.join(outdir, "%(title)s.%(ext)s")

    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    # Add cookies if available
    cookies_path = os.getenv("COOKIES_FILE_PATH")
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
        logger.info(f"Using cookies file: {cookies_path}")
    else:
        logger.warning("No cookies file found; login-required videos may fail.")

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        ext = info.get("ext") or "mp4"
        title = info.get("title") or "video"
        filename = os.path.join(outdir, f"{title}.{ext}")

        possible = info.get("_filename")
        if possible and os.path.exists(possible):
            return possible

        if os.path.exists(filename):
            return filename

        files = sorted(
            [os.path.join(outdir, f) for f in os.listdir(outdir)],
            key=os.path.getmtime,
            reverse=True
        )
        if files:
            return files[0]

        raise FileNotFoundError("Downloaded file not found.")

def extract_playlist_items(url: str):
    ydl_opts = {"quiet": True, "extract_flat": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info:
            items = info["entries"]
            # items may contain 'url' or 'id'
            videos = []
            for e in items:
                # create full video url
                vid = e.get("id") or e.get("url")
                if not vid:
                    continue
                videos.append(f"https://www.youtube.com/watch?v={vid}")
            return videos
        else:
            # Not a playlist
            return [url]

# --- Bot startup ---
app = Client("ytbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Buttons
start_buttons = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("Help", callback_data="help")],
        [InlineKeyboardButton("Owner", url=f"https://t.me/{os.getenv('OWNER_USERNAME','')}")] if os.getenv("OWNER_USERNAME") else []
    ]
)

# --- Handlers ---
@app.on_message(filters.command("start"))
async def cmd_start(cli, msg):
    uid = msg.from_user.id if msg.from_user else None
    caption = "HELLO 👋\nI am your private YT downloader bot.\nSend /help to see commands.\n\nCreated by @BLACKRHINO360"
    await send_photo_via_url_or_upload(cli, msg.chat.id, DEFAULT_IMG, caption=caption, reply_markup=start_buttons)

@app.on_message(filters.command("help"))
async def cmd_help(cli, msg):
    text = """
Available commands:
/start - Start & info
/help - This help
/ytvid - Download single YouTube video (interactive)
/ytpl  - Download playlist (interactive)
Note: Bot is private. Only owner / sudo users can use heavy commands.
Created by @BLACKRHINO360
"""
    await msg.reply_text(text)

# Interactive: single video
@app.on_message(filters.command("ytvid"))
async def cmd_ytvid(cli, msg):
    uid = msg.from_user.id if msg.from_user else None
    if not is_authorized(uid):
        return await msg.reply_text("🚫 You are not authorized to use this bot.")
    try:
        await msg.reply_text("Send the YouTube video URL now (or paste).")
        # wait for user response
        m = await app.listen(msg.chat.id, timeout=60)
        url = m.text.strip()
        await msg.reply_text("Which quality? Example: best, best[height<=720]. Send `best` to keep default.")
        qmsg = await app.listen(msg.chat.id, timeout=30)
        quality = qmsg.text.strip() if (qmsg and qmsg.text) else "best"
    except Exception as e:
        await msg.reply_text("Timeout or error: " + str(e))
        return

    m2 = await msg.reply_text("Downloading... This may take a while.")
    try:
        out = download_video_with_ydl(url, TMP_DIR, fmt=quality)
        # send file
        basename = os.path.basename(out)
        filesize = os.path.getsize(out)
        if filesize > 50 * 1024 * 1024:
            # >50MB send as document (Telegram may still limit). For big files Render may fail.
            await msg.reply_document(document=out, caption=f"Downloaded: {basename}")
        else:
            await msg.reply_video(video=out, caption=f"Downloaded: {basename}")
    except Exception as e:
        logger.exception("Download failed: %s", e)
        await msg.reply_text("Download failed: " + str(e))
    finally:
        try:
            if os.path.exists(out):
                os.remove(out)
        except:
            pass
        await m2.delete()

# Interactive: playlist
@app.on_message(filters.command("ytpl"))
async def cmd_ytpl(cli, msg):
    uid = msg.from_user.id if msg.from_user else None
    if not is_authorized(uid):
        return await msg.reply_text("🚫 You are not authorized to use this bot.")
    try:
        await msg.reply_text("Send the YouTube playlist URL now.")
        m = await app.listen(msg.chat.id, timeout=60)
        url = m.text.strip()
        await msg.reply_text("Which quality for videos? Example: best, best[height<=720]. Send `best` to keep default.")
        qmsg = await app.listen(msg.chat.id, timeout=30)
        quality = qmsg.text.strip() if (qmsg and qmsg.text) else "best"
    except Exception as e:
        await msg.reply_text("Timeout or error: " + str(e))
        return

    status_msg = await msg.reply_text("Fetching playlist...")
    try:
        videos = extract_playlist_items(url)
        await status_msg.edit_text(f"Found {len(videos)} videos. Starting download one by one (this will take time).")
        counter = 0
        for vurl in videos:
            counter += 1
            try:
                s = await msg.reply_text(f"[{counter}/{len(videos)}] Downloading...")
                out = download_video_with_ydl(vurl, TMP_DIR, fmt=quality)
                basename = os.path.basename(out)
                filesize = os.path.getsize(out)
                if filesize > 50 * 1024 * 1024:
                    await msg.reply_document(document=out, caption=f"{counter}/{len(videos)} {basename}")
                else:
                    await msg.reply_video(video=out, caption=f"{counter}/{len(videos)} {basename}")
                try:
                    os.remove(out)
                except:
                    pass
                await s.delete()
            except Exception as e:
                await msg.reply_text(f"Failed for video {vurl}: {e}")
        await status_msg.edit_text("Playlist processing finished.")
    except Exception as e:
        logger.exception("Playlist failed: %s", e)
        await status_msg.edit_text("Playlist failed: " + str(e))

# /sudo add or remove owner convenience (only OWNER_ID)
@app.on_message(filters.command("sudo") & filters.user(OWNER_ID))
async def cmd_sudo(cli, msg):
    # usage: /sudo add 12345  or /sudo remove 12345
    args = msg.text.split()
    if len(args) < 3:
        return await msg.reply_text("Usage: /sudo add <user_id>  OR  /sudo remove <user_id>")
    action = args[1].lower()
    try:
        uid = int(args[2])
    except:
        return await msg.reply_text("User id must be integer.")
    global SUDO_USERS
    if action == "add":
        if uid not in SUDO_USERS:
            SUDO_USERS.append(uid)
            await msg.reply_text(f"Added {uid} to sudo users.")
        else:
            await msg.reply_text("Already present.")
    elif action == "remove":
        if uid in SUDO_USERS and uid != OWNER_ID:
            SUDO_USERS.remove(uid)
            await msg.reply_text(f"Removed {uid} from sudo users.")
        else:
            await msg.reply_text("Cannot remove.")
    else:
        await msg.reply_text("Unknown action. use add/remove.")

# --- Start both services ---
def start():
    # start web server thread first
    run_web()
    # run pyrogram client (blocking)
    logger.info("Starting Pyrogram client...")
    app.run()  # blocks

if __name__ == "__main__":
    # required env check
    missing = []
    if API_ID == 0: missing.append("API_ID")
    if not API_HASH: missing.append("API_HASH")
    if not BOT_TOKEN: missing.append("BOT_TOKEN")
    if OWNER_ID == 0: missing.append("OWNER_ID")
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        print("Set env vars:", ", ".join(missing))
        raise SystemExit(1)
    try:
        start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        try:
            shutil.rmtree(TMP_DIR)
        except:
            pass

