# bot.py
import os
import asyncio
import tempfile
import shutil
import logging
import shlex
import subprocess
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pyromod import listen
from yt_dlp import YoutubeDL

from web import run_web
import aiohttp
from aiohttp import ClientTimeout

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ytbot")

# --- Environment / secrets (Render dashboard me set karna hota hai) ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

SUDO_ENV = os.getenv("SUDO_USERS", "")
PORT = int(os.getenv("PORT", 10000))

SUDO_USERS = []
if SUDO_ENV:
    for v in SUDO_ENV.split(","):
        try:
            SUDO_USERS.append(int(v.strip()))
        except:
            pass
SUDO_USERS = list(set(SUDO_USERS + [OWNER_ID]))

DEFAULT_IMG = os.getenv(
    "DEFAULT_IMG",
    "https://graph.org/file/5ed50675df0faf833efef-e102210eb72c1d5a17.jpg"
)

TMP_DIR = tempfile.mkdtemp(prefix="ytbot_")
logger.info("TMP DIR: %s", TMP_DIR)

# Quality settings
RESOLUTIONS = [144,240,360,480,720,1080]
PENDING_QUALITY = {}
MAX_VIDEO_BYTES = 50 * 1024 * 1024   # 50 MB


# ---------------- AUTH ----------------
def is_authorized(uid: int) -> bool:
    return uid in SUDO_USERS


# ---------------- IMAGE SENDER ----------------
async def send_photo_via_url_or_upload(bot, chat_id, url, caption=None, reply_markup=None):
    logger.info("Attempting image: %s", url)
    try:
        return await bot.send_photo(chat_id, url, caption=caption, reply_markup=reply_markup)
    except:
        pass

    timeout = aiohttp.ClientTimeout(total=20)
    tmp_path = os.path.join(TMP_DIR, "tmp.jpg")
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await bot.send_message(chat_id, "Image failed.")
                data = await resp.read()
                with open(tmp_path, "wb") as f:
                    f.write(data)
        await bot.send_photo(chat_id, tmp_path, caption=caption, reply_markup=reply_markup)
    except Exception:
        await bot.send_message(chat_id, "Image fetch error.")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# --------------- QUALITY BUTTON UI -----------------
def quality_keyboard():
    rows = []
    row = []
    for i, r in enumerate(RESOLUTIONS, 1):
        row.append(InlineKeyboardButton(str(r), callback_data=f"res:{r}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@app.on_callback_query()
async def callback_handler(cli: Client, cq: CallbackQuery):
    data = cq.data or ""
    if data.startswith("res:"):
        h = int(data.split(":")[1])
        fut = PENDING_QUALITY.get(cq.message.chat.id)
        if fut and not fut.done():
            fut.set_result(h)
        await cq.answer(f"Selected {h}p")
    else:
        await cq.answer()


# ---------------- FAST RE-ENCODE ----------------
def reencode_to_target_size_singlepass(src: str, dst: str, target_bytes: int, audio_kbps=64) -> str:
    try:
        cmd = (
            f"ffprobe -v error -show_entries format=duration "
            f"-of default=noprint_wrappers=1:nokey=1 {shlex.quote(src)}"
        )
        dur = float(subprocess.check_output(cmd, shell=True).decode().strip())
    except:
        # fallback crf encode
        cmd = (
            f"ffmpeg -y -i {shlex.quote(src)} -c:v libx264 "
            f"-preset veryfast -crf 28 -c:a aac -b:a {audio_kbps}k {shlex.quote(dst)}"
        )
        subprocess.check_call(cmd, shell=True)
        return dst

    audio_bps = audio_kbps * 1000
    audio_bytes = (audio_bps/8) * dur
    video_bytes_target = max(target_bytes - audio_bytes, 150000)
    video_kbps = int((video_bytes_target*8/dur)/1000)
    if video_kbps < 100: video_kbps = 100
    if video_kbps > 5000: video_kbps = 5000

    cmd = (
        f"ffmpeg -y -i {shlex.quote(src)} -c:v libx264 -b:v {video_kbps}k "
        f"-preset veryfast -c:a aac -b:a {audio_kbps}k {shlex.quote(dst)}"
    )
    subprocess.check_call(cmd, shell=True)
    return dst


# ---------------- YOUTUBE DOWNLOAD (QUALITY-SELECTED) ----------------
def download_video_with_ydl(url: str, outdir: str, req_height: int=None):
    outtmpl = os.path.join(outdir, "%(title)s.%(ext)s")

    base = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    cookies = os.getenv("COOKIES_FILE_PATH")
    if cookies and os.path.exists(cookies):
        base["cookiefile"] = cookies
        logger.info("Using cookies...")

    def finalize(info):
        f1 = info.get("_filename")
        if f1 and os.path.exists(f1):
            return f1, info
        ext = info.get("ext","mp4")
        title = info.get("title","video")
        p = os.path.join(outdir, f"{title}.{ext}")
        if os.path.exists(p):
            return p, info
        # fallback
        files = sorted(
            [os.path.join(outdir,f) for f in os.listdir(outdir)],
            key=os.path.getmtime, reverse=True
        )
        if files: return files[0], info
        raise FileNotFoundError("No downloaded file found.")

    def run(opts):
        with YoutubeDL(opts) as y:
            return y.extract_info(url, download=True)

    # 1) try progressive
    if req_height:
        f1 = f"best[height<={req_height}]"
        try:
            info = run({**base,"format":f1})
            return finalize(info)
        except Exception as e:
            logger.warning("Progressive failed: %s", e)

    # 2) try adaptive merge
    if req_height:
        f2 = f"bestvideo[height<={req_height}]+bestaudio/best"
        try:
            info = run({**base,"format":f2})
            return finalize(info)
        except Exception as e:
            logger.warning("Adaptive failed: %s", e)

    # 3) best
    try:
        info = run({**base,"format":"best"})
        return finalize(info)
    except Exception as e:
        logger.warning("Best failed: %s", e)

    raise RuntimeError("No suitable format available.")


# ---------------- PLAYLIST EXTRACT ----------------
def extract_playlist_items(url: str):
    with YoutubeDL({"quiet":True,"extract_flat":True}) as y:
        info = y.extract_info(url, download=False)
        if "entries" in info:
            vids=[]
            for e in info["entries"]:
                vid = e.get("id") or e.get("url")
                if vid:
                    vids.append(f"https://www.youtube.com/watch?v={vid}")
            return vids
        return [url]


# ---------------- BOT ----------------
app = Client("ytbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# /start
@app.on_message(filters.command("start"))
async def start_handler(cli, msg):
    caption = "👋 **Private YT Downloader Bot**\n\n✔ Single Video\n✔ Playlists\n✔ Quality Select (144–1080)\n✔ >50MB videos as VIDEO\n\nCreated by @BLACKRHINO360"
    await send_photo_via_url_or_upload(cli, msg.chat.id, DEFAULT_IMG, caption, None)


# /help
@app.on_message(filters.command("help"))
async def help_handler(cli, msg):
    text = (
        "**Commands**\n"
        "/ytvid – Single video\n"
        "/ytpl – Playlist\n"
        "/sudo add <id>\n"
        "/sudo remove <id>\n"
        "\nCreated by @BLACKRHINO360"
    )
    await msg.reply_text(text)


# ---------------- YT VID ----------------
@app.on_message(filters.command("ytvid"))
async def ytvid_handler(cli, msg):
    if not is_authorized(msg.from_user.id):
        return await msg.reply_text("🚫 Unauthorized.")

    await msg.reply_text("🎬 Send YouTube video link:")
    m = await app.listen(msg.chat.id, timeout=180)
    url = m.text.strip()

    fut = asyncio.get_event_loop().create_future()
    PENDING_QUALITY[msg.chat.id] = fut
    await msg.reply_text("Select quality:", reply_markup=quality_keyboard())

    try:
        req_height = await asyncio.wait_for(fut, timeout=60)
    except:
        return await msg.reply_text("No quality selected.")
    finally:
        PENDING_QUALITY.pop(msg.chat.id, None)

    info_msg = await msg.reply_text("⏳ Downloading...")
    try:
        file_path, info = await asyncio.to_thread(download_video_with_ydl, url, TMP_DIR, req_height)
    except Exception as e:
        return await info_msg.edit_text("❌ Download failed: " + str(e))

    # re-encode if >50MB
    try:
        size = os.path.getsize(file_path)
        if size > MAX_VIDEO_BYTES:
            await info_msg.edit_text("Video >50MB — reencoding...")
            dst = str(Path(file_path).with_name(Path(file_path).stem+"_small.mp4"))
            try:
                await asyncio.to_thread(reencode_to_target_size_singlepass, file_path, dst, MAX_VIDEO_BYTES-1024*1024)
                send_path = dst
            except Exception as e:
                send_path = file_path
        else:
            send_path = file_path

        await msg.reply_video(send_path, caption=info.get("title",""))
    except:
        await msg.reply_text("Failed to send video.")
    finally:
        try: os.remove(file_path)
        except: pass
        try:
            if 'dst' in locals() and os.path.exists(dst):
                os.remove(dst)
        except: pass
        await info_msg.delete()


# ---------------- YT PLAYLIST ----------------
@app.on_message(filters.command("ytpl"))
async def ytpl_handler(cli, msg):
    if not is_authorized(msg.from_user.id):
        return await msg.reply_text("🚫 Unauthorized.")

    await msg.reply_text("📄 Send playlist link:")
    m = await app.listen(msg.chat.id, timeout=180)
    url = m.text.strip()

    fut = asyncio.get_event_loop().create_future()
    PENDING_QUALITY[msg.chat.id] = fut
    await msg.reply_text("Select quality:", reply_markup=quality_keyboard())

    try:
        req_height = await asyncio.wait_for(fut, timeout=60)
    except:
        return await msg.reply_text("No quality selected.")
    finally:
        PENDING_QUALITY.pop(msg.chat.id, None)

    status = await msg.reply_text("⏳ Fetching playlist...")
    try:
        videos = extract_playlist_items(url)
    except Exception as e:
        return await status.edit_text("❌ Failed: "+str(e))

    await status.edit_text(f"Found {len(videos)} videos. Starting...")

    for idx, vurl in enumerate(videos, start=1):
        s = await msg.reply_text(f"[{idx}/{len(videos)}] Downloading...")
        try:
            file_path, info = await asyncio.to_thread(download_video_with_ydl, vurl, TMP_DIR, req_height)
            size = os.path.getsize(file_path)

            if size > MAX_VIDEO_BYTES:
                dst = str(Path(file_path).with_name(Path(file_path).stem+"_small.mp4"))
                try:
                    await asyncio.to_thread(reencode_to_target_size_singlepass, file_path, dst, MAX_VIDEO_BYTES-1024*1024)
                    send_path = dst
                except:
                    send_path = file_path
            else:
                send_path = file_path

            await msg.reply_video(send_path, caption=f"{idx}/{len(videos)} – {info.get('title','')}")
        except Exception as e:
            await msg.reply_text(f"Failed ({idx}): {e}")
        finally:
            try: os.remove(file_path)
            except: pass
            try:
                if 'dst' in locals() and os.path.exists(dst):
                    os.remove(dst)
            except: pass
            await s.delete()
            await asyncio.sleep(1)

    await status.edit_text("Playlist completed ✔")


# ---------------- SUDO ----------------
@app.on_message(filters.command("sudo") & filters.user(OWNER_ID))
async def sudo_handler(cli, msg):
    args = msg.text.split()
    if len(args) < 3:
        return await msg.reply_text("Use: /sudo add <id>  or  /sudo remove <id>")

    action = args[1]
    try:
        uid = int(args[2])
    except:
        return await msg.reply_text("User ID must be number.")

    if action == "add":
        if uid not in SUDO_USERS:
            SUDO_USERS.append(uid)
            await msg.reply_text(f"Added {uid}.")
        else:
            await msg.reply_text("Already added.")
    elif action == "remove":
        if uid != OWNER_ID and uid in SUDO_USERS:
            SUDO_USERS.remove(uid)
            await msg.reply_text(f"Removed {uid}.")
        else:
            await msg.reply_text("Cannot remove.")
    else:
        await msg.reply_text("Invalid action.")



# ---------------- MAIN START ----------------
def start():
    run_web()
    logger.info("Starting bot...")
    app.run()


if __name__ == "__main__":
    missing=[]
    if API_ID==0: missing.append("API_ID")
    if not API_HASH: missing.append("API_HASH")
    if not BOT_TOKEN: missing.append("BOT_TOKEN")
    if OWNER_ID==0: missing.append("OWNER_ID")

    if missing:
        print("Missing:",missing)
        raise SystemExit

    try:
        start()
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
