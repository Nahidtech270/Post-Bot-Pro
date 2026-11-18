# handlers/admin.py
import io
import os
import re
import html
import json
import time
import uuid
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, CallbackQuery
import config
from database import db, get_setting, set_setting
from tmdb import search_tmdb, fetch_tmdb_details
from tg_api import send_colored_msg, edit_colored_msg

ADMIN_FILTER = filters.user(config.ADMIN_IDS) if config.ADMIN_IDS else filters.private
TASKS = {}
STATES = {}
_BOT_USERNAME = None

LANGUAGES = ["Bangla", "Hindi Dubbed", "Dual Audio", "South Indian", "English", "Korean"]
PRESET_CATEGORIES = [
    "Viral", "Bangla", "Hindi Dubbed", "South Indian",
    "Hollywood", "Web Series", "Action", "Horror",
    "Comedy", "Thriller", "Sci-Fi", "Anime",
    "HD", "1080p", "4K", "WEB-DL"
]


def humanbytes(size):
    if not size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.2f} {unit}"


def format_eta(seconds):
    if seconds <= 0 or seconds > 86400:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def get_progress_text(title_label: str, current: int, total: int, start_time: float):
    pct = (current / total) * 100 if total > 0 else 0
    filled = int(pct // 10)
    bar = f"[{'█' * filled}{'░' * (10 - filled)}] {pct:.1f}%"
    elapsed = max(time.time() - start_time, 0.1)
    speed = current / elapsed
    eta = (total - current) / speed if speed > 0 and total > current else 0
    return (
        f"<b>{title_label}</b>\n\n"
        f"<code>{bar}</code>\n"
        f"💾 {humanbytes(current)} / {humanbytes(total)}\n"
        f"⚡ স্পিড: {humanbytes(speed)}/s | ⏱️ বাকি: {format_eta(eta)}"
    )


class ProgressFile(io.IOBase):
    def __init__(self, path, loop, callback):
        self._f = open(path, "rb")
        self.total = os.path.getsize(path)
        self.uploaded = 0
        self.loop = loop
        self.callback = callback
        self.last_report = 0
        self.done = False

    def read(self, size=-1):
        data = self._f.read(size)
        if data and not self.done:
            self.uploaded += len(data)
            now = time.time()
            if now - self.last_report >= 3.5:
                self.last_report = now
                u, t = self.uploaded, self.total
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.callback(u, t)) if not self.done else None
                )
        return data

    def seek(self, offset, whence=0):
        return self._f.seek(offset, whence)

    def tell(self):
        return self._f.tell()

    def readable(self):
        return True

    def fileno(self):
        return self._f.fileno()

    def close(self):
        self.done = True
        return self._f.close()


async def get_bot_username(client: Client):
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await client.get_me()
        _BOT_USERNAME = me.username
    return _BOT_USERNAME


async def get_tmdb_key():
    doc = await get_setting("tmdb")
    if doc and doc.get("api_key"):
        return doc["api_key"]
    return config.TMDB_API_KEY or None


async def get_streamtape_creds():
    doc = await get_setting("streamtape")
    if doc and doc.get("login") and doc.get("key"):
        return doc["login"], doc["key"]
    return None, None


async def upload_to_streamtape(file_path, file_name, api_login, api_key, status_msg=None, label_fn=None):
    upload_server_url = None
    timeout = aiohttp.ClientTimeout(total=1800)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"https://api.streamtape.com/file/ul?login={api_login}&key={api_key}") as resp:
            data = await resp.json()
            if data.get("status") == 200:
                upload_server_url = data["result"]["url"]
    if not upload_server_url:
        return None

    loop = asyncio.get_running_loop()
    up_start = time.time()

    async def on_upload_progress(cur, tot):
        if status_msg:
            try:
                lbl = label_fn() if callable(label_fn) else str(label_fn or "☁️ Streamtape-এ আপলোড হচ্ছে...")
                await status_msg.edit_text(get_progress_text(lbl, cur, tot, up_start), parse_mode=ParseMode.HTML)
            except Exception:
                pass

    raw = ""
    async with aiohttp.ClientSession(timeout=timeout) as session:
        pf = ProgressFile(file_path, loop, on_upload_progress)
        try:
            form = aiohttp.FormData()
            form.add_field("file1", pf, filename=file_name, content_type="application/octet-stream")
            async with session.post(upload_server_url, data=form) as resp:
                raw = await resp.text()
        finally:
            pf.close()

    try:
        res = json.loads(raw)
        if str(res.get("status")) == "200":
            return res.get("result", {}).get("id")
    except Exception:
        pass
    m = re.search(r'"id"\s*:\s*"([^"]+)"', raw)
    return m.group(1) if m else None


async def download_with_progress(target_msg: Message, status_msg: Message, label_fn):
    dl_start = time.time()
    last_edit = 0

    async def prog(cur, tot):
        nonlocal last_edit
        now = time.time()
        if now - last_edit >= 3.0:
            last_edit = now
            try:
                lbl = label_fn() if callable(label_fn) else str(label_fn)
                await status_msg.edit_text(get_progress_text(lbl, cur, tot, dl_start), parse_mode=ParseMode.HTML)
            except Exception:
                pass

    return await target_msg.download(progress=prog)


def compute_labels_list(task: dict):
    # ১০০% ম্যানুয়াল: ইউজার যেগুলো নিজে দিয়েছে শুধু সেগুলোই থাকবে!
    lbls = list(task.get("labels", []))
    if task.get("is_adult") and "Adult" not in lbls:
        lbls.append("Adult")
    elif not task.get("is_adult") and "Adult" in lbls:
        lbls.remove("Adult")
    return list(dict.fromkeys([x.strip() for x in lbls if x.strip()]))


def build_draft_keyboard(task_id: str, task: dict):
    emb_c = len(task.get("embeds", []))
    dl_c = len(task.get("downloads", []))
    lang = task.get("language") or "Not Set (Optional)"
    is_adult = task.get("is_adult", False)
    adult_txt = "🔞 18+ Adult: YES" if is_adult else "🔞 18+ Adult: NO"
    lbl_c = len(compute_labels_list(task))
    mode = task.get("post_mode", "vertical")
    mode_txt = "📺 View: Vertical (Video 1, 2..)" if mode == "vertical" else "🔄 View: Server Tabs (Movie)"

    rows = [
        [{"text": mode_txt, "callback_data": f"mb_mode|{task_id}", "style": "success"}],
        [
            {"text": f"🗣️ {lang}", "callback_data": f"mb_langm|{task_id}", "style": "primary"},
            {"text": adult_txt, "callback_data": f"mb_adult|{task_id}", "style": "danger" if is_adult else "primary"}
        ],
        [{"text": f"🏷️ Categories / Labels ({lbl_c})", "callback_data": f"mb_lblm|{task_id}", "style": "primary"}],
        [{"text": f"🎬 Add Bulk Videos to Streamtape ({emb_c})", "callback_data": f"mb_st|{task_id}", "style": "danger"}],
        [{"text": f"🔗 Paste Embed URL / iframe ({emb_c})", "callback_data": f"mb_emb|{task_id}", "style": "primary"}],
    ]
    if mode != "vertical" or dl_c > 0:
        rows.append([{"text": f"📁 Add Telegram File ({dl_c})", "callback_data": f"mb_tg|{task_id}", "style": "primary"}])

    rows.append([{"text": "✅ Finish & Get Preview + Copy Code", "callback_data": f"mb_done|{task_id}", "style": "success"}])
    return rows


def build_category_keyboard(task_id: str, task: dict):
    current = set(compute_labels_list(task))
    rows = []
    row = []
    for cat in PRESET_CATEGORIES:
        is_on = cat in current
        row.append({
            "text": f"{'✅' if is_on else '➕'} {cat}",
            "callback_data": f"mb_tglbl|{task_id}|{cat}",
            "style": "success" if is_on else "primary"
        })
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "✏️ Type Custom Label", "callback_data": f"mb_clbl|{task_id}", "style": "danger"},
        {"text": "🔙 Back to Draft", "callback_data": f"mb_back|{task_id}", "style": "success"}
    ])
    return rows


def build_language_keyboard(task_id: str, task: dict):
    cur = task.get("language", "")
    rows = []
    row = []
    for lang in LANGUAGES:
        is_on = (lang == cur)
        row.append({
            "text": f"{'✅ ' if is_on else ''}{lang}",
            "callback_data": f"mb_setl|{task_id}|{lang}",
            "style": "success" if is_on else "primary"
        })
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "✏️ Custom Language", "callback_data": f"mb_clang|{task_id}", "style": "danger"},
        {"text": "🗑️ Clear Language", "callback_data": f"mb_clrl|{task_id}", "style": "danger"}
    ])
    rows.append([{"text": "🔙 Back", "callback_data": f"mb_back|{task_id}", "style": "success"}])
    return rows


def draft_summary_text(task: dict):
    labels_str = ", ".join(compute_labels_list(task)) or "None (Click Categories to select)"
    lang = task.get("language") or "None"
    mode = "Vertical (Video 1, 2, 3...)" if task.get("post_mode", "vertical") == "vertical" else "Server Tabs (Movie)"
    return (
        f"🎬 <b>ড্রাফট রেডি:</b> <code>{html.escape(task.get('title', 'Untitled'))}</code>\n"
        f"📺 <b>Mode:</b> {mode}\n"
        f"🗣️ <b>Language:</b> {html.escape(lang)}\n"
        f"🏷️ <b>Labels:</b> <code>{html.escape(labels_str)}</code>\n"
        f"▶️ <b>Videos:</b> {len(task.get('embeds', []))} | 📁 <b>Download Files:</b> {len(task.get('downloads', []))}\n\n"
        f"👇 নিচের বাটন থেকে ক্যাটাগরি ও ভিডিও যুক্ত করুন:"
    )


async def generate_final_preview(client: Client, chat_id: int, task_id: str, task: dict):
    title = html.escape(task.get("title", "Untitled Video"))
    poster_url = task.get("poster_url") or "https://via.placeholder.com/300x450/151924/9ca3af?text=No+Poster"
    language = task.get("language", "")
    genre = task.get("genre", "")
    rating = str(task.get("rating", ""))
    runtime = str(task.get("runtime", ""))
    year = str(task.get("year", ""))
    cast = task.get("cast", "")
    storyline = task.get("storyline", "")
    trailer_id = task.get("trailer_id", "")
    screenshots = task.get("screenshots", [])

    info_items = f'    <li><strong>Title:</strong> {title}</li>\n'
    if language:
        info_items += f'    <li><strong>Language:</strong> {html.escape(language)}</li>\n'
    if genre:
        info_items += f'    <li><strong>Genre:</strong> {html.escape(genre)}</li>\n'
    if rating and rating != "N/A":
        info_items += f'    <li><strong>IMDb Rating:</strong> &#11088; {html.escape(rating)}/10</li>\n'
    if runtime and runtime != "N/A":
        info_items += f'    <li><strong>Runtime:</strong> {html.escape(runtime)}</li>\n'
    if year and year != "N/A":
        info_items += f'    <li><strong>Release Year:</strong> {html.escape(year)}</li>\n'
    if cast and cast != "N/A":
        info_items += f'    <li><strong>Cast:</strong> {html.escape(cast)}</li>\n'

    story_html = ""
    if storyline:
        story_html = f'<h3>&#128214; Storyline</h3>\n<p>{html.escape(storyline)}</p>\n\n'

    trailer_html = ""
    if trailer_id:
        trailer_html = (
            '<h3>&#127902; Official Trailer</h3>\n'
            '<div class="trailer-box">\n'
            f'  <iframe src="https://www.youtube.com/embed/{html.escape(trailer_id)}" allowfullscreen="allowfullscreen"></iframe>\n'
            '</div>\n\n'
        )

    ss_html = ""
    if screenshots:
        ss_imgs = "".join([f'<img src="{s}" alt="{title} Screenshot" loading="lazy"/>' for s in screenshots])
        ss_html = f'<h3>&#128248; Screenshots</h3>\n<div class="screenshots-grid">{ss_imgs}</div>\n\n'

    embed_section_html = ""
    embeds = task.get("embeds", [])
    mode = task.get("post_mode", "vertical")

    if embeds:
        if mode == "vertical":
            v_items = ""
            for idx, emb in enumerate(embeds, start=1):
                emb_val = html.escape(emb["url"], quote=True)
                v_items += (
                    '<div class="vertical-video-item">\n'
                    f'  <h4>&#127916; Video {idx}</h4>\n'
                    f'  <div class="embed-player-frame" data-embed="{emb_val}"></div>\n'
                    '</div>\n'
                )
            embed_section_html = (
                '<div class="embed-section">\n'
                '  <h3>&#127916; Watch Online</h3>\n'
                f'  <div class="vertical-videos-list">\n{v_items}  </div>\n'
                '</div>\n'
            )
        else:
            tabs_html = ""
            for idx, emb in enumerate(embeds, start=1):
                active_cls = " active" if idx == 1 else ""
                srv_name = f"Server {idx} (HD)"
                emb_val = html.escape(emb["url"], quote=True)
                tabs_html += f'<button type="button" class="embed-tab-btn{active_cls}" data-embed="{emb_val}" onclick="loadThemeEmbed(this)">&#9654; {srv_name}</button>\n'

            embed_section_html = (
                '<div class="embed-section">\n'
                '  <h3>&#127916; Watch Online (Stream Player)</h3>\n'
                f'  <div class="embed-tabs">\n{tabs_html}  </div>\n'
                '  <div class="embed-player-frame"></div>\n'
                '</div>\n'
            )

    dl_section_html = ""
    downloads = task.get("downloads", [])
    if downloads:
        btn_styles = ["dl-btn alt", "dl-btn", "dl-btn rgb"]
        btns_html = ""
        for idx, dl in enumerate(downloads):
            cls = btn_styles[idx % len(btn_styles)]
            q_label = html.escape(dl["quality"])
            sz = html.escape(dl["size"])
            tg_url = html.escape(dl["link"], quote=True)
            btns_html += f'<a href="{tg_url}" class="{cls}" target="_blank">&#11015; Download {q_label} ({sz})</a>\n'

        dl_section_html = (
            '<h3>&#11015; Download Links (Telegram Files)</h3>\n'
            f'<div class="dl-buttons">\n{btns_html}</div>\n'
        )

    dl_box_html = ""
    if embed_section_html or dl_section_html:
        dl_box_html = (
            '<div class="download-box">\n'
            f'  {embed_section_html}'
            f'  {dl_section_html}'
            '</div>'
        )

    blogger_html = (
        f'<div class="movie-hero-box">\n'
        f'  <div class="movie-hero-poster"><img src="{poster_url}" alt="{title}"/></div>\n'
        f'  <ul class="movie-info-list">\n'
        f'{info_items}'
        f'  </ul>\n'
        f'</div>\n\n'
        f'{story_html}'
        f'{trailer_html}'
        f'{ss_html}'
        f'{dl_box_html}'
    )

    labels_str = ", ".join(compute_labels_list(task))

    preview_id = f"p_{task_id}"
    if db is not None:
        await db.previews.update_one(
            {"_id": preview_id},
            {"$set": {
                "title": task.get("title", "Video"),
                "labels": labels_str,
                "html": blogger_html,
                "created_at": time.time()
            }},
            upsert=True
        )

    preview_url = f"{config.APP_URL}/preview/{preview_id}"
    kb = [[{"text": "🌐 Live Preview & Copy Code", "url": preview_url, "style": "success"}]]

    await send_colored_msg(
        chat_id,
        f"🎉 <b>পোস্ট কোড সফলভাবে তৈরি হয়েছে!</b>\n\n"
        f"🎬 <b>Title:</b> <code>{title}</code>\n"
        f"🏷 <b>Labels:</b> <code>{html.escape(labels_str) or 'None'}</code>\n\n"
        f"👇 নিচের সবুজ বাটনে ক্লিক করে লাইভ প্রিভিউ দেখুন এবং ১-ক্লিকে কোড কপি করুন:\n"
        f"🔗 {preview_url}",
        kb
    )


# ----------------- [ ব্যাচ কিউ প্রসেসর (একসাথে সব ভিডিও আপলোড) ] -----------------

async def process_batch_queue(client: Client, chat_id: int, uid: int, task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return

    st_l, st_k = await get_streamtape_creds()
    status_msg = await client.send_message(chat_id, "⏳ <b>ব্যাচ আপলোড কিউ শুরু হচ্ছে...</b>", parse_mode=ParseMode.HTML)

    try:
        while task.get("queue"):
            if task_id not in TASKS:
                break

            msg = task["queue"].pop(0)
            media = msg.video or msg.document
            fname = getattr(media, "file_name", "video.mp4") or "video.mp4"
            done_count = len(task["embeds"])
            cur_num = done_count + 1

            def total_now():
                return len(task["embeds"]) + 1 + len(task.get("queue", []))

            fpath = None
            try:
                await status_msg.edit_text(
                    f"📥 <b>ভিডিও {cur_num}/{total_now()} ডাউনলোড শুরু হচ্ছে...</b>",
                    parse_mode=ParseMode.HTML
                )
                fpath = await download_with_progress(
                    msg, status_msg,
                    lambda c=cur_num: f"📥 ভিডিও {c}/{total_now()} ডাউনলোড হচ্ছে..."
                )
                await status_msg.edit_text(
                    f"☁️ <b>ভিডিও {cur_num}/{total_now()} Streamtape-এ আপলোড হচ্ছে...</b>",
                    parse_mode=ParseMode.HTML
                )
                fid = await upload_to_streamtape(
                    fpath, fname, st_l, st_k,
                    status_msg=status_msg,
                    label_fn=lambda c=cur_num: f"☁️ ভিডিও {c}/{total_now()} Streamtape-এ আপলোড হচ্ছে..."
                )
                if fid:
                    task["embeds"].append({"url": f"https://streamta.pe/e/{fid}"})
                else:
                    await client.send_message(chat_id, f"⚠️ ভিডিও {cur_num} (`{fname}`) আপলোড ব্যর্থ হয়েছে, পরেরটিতে যাচ্ছি...")
            except Exception as e:
                await client.send_message(chat_id, f"⚠️ ভিডিও {cur_num} এরর: {e}")
            finally:
                if fpath and os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
    finally:
        task["queue_running"] = False
        if STATES.get(uid, {}).get("step") == "waiting_batch_videos":
            STATES.pop(uid, None)
        try:
            await status_msg.delete()
        except Exception:
            pass
        if task_id in TASKS:
            await send_colored_msg(
                chat_id,
                f"✅ <b>সব ভিডিও আপলোড সম্পন্ন! (মোট: {len(task['embeds'])} টি)</b>\n\n" + draft_summary_text(task),
                build_draft_keyboard(task_id, task)
            )


# ----------------- [ সেটিংস ও স্ট্যাটাস কমান্ড ] -----------------

@Client.on_message(filters.command("set_tmdb") & ADMIN_FILTER & filters.private)
async def cmd_set_tmdb(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("⚙️ নিয়ম: <code>/set_tmdb YOUR_TMDB_API_KEY</code>", parse_mode=ParseMode.HTML)
        return
    await set_setting("tmdb", {"api_key": message.command[1].strip()})
    await message.reply_text("✅ <b>TMDB API Key সেভ হয়েছে!</b>", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("set_player") & ADMIN_FILTER & filters.private)
async def cmd_set_player(client: Client, message: Message):
    if len(message.command) < 3:
        await message.reply_text("⚙️ নিয়ম: <code>/set_player API_LOGIN API_KEY</code>", parse_mode=ParseMode.HTML)
        return
    await set_setting("streamtape", {"login": message.command[1].strip(), "key": message.command[2].strip()})
    await message.reply_text("✅ <b>Streamtape অ্যাকাউন্ট সেভ হয়েছে!</b>", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("set_fsub") & ADMIN_FILTER & filters.private)
async def cmd_set_fsub(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("⚙️ নিয়ম:\n• চালু: <code>/set_fsub @ChannelUsername</code>\n• বন্ধ: <code>/set_fsub off</code>", parse_mode=ParseMode.HTML)
        return
    val = message.command[1].strip()
    if val.lower() == "off":
        await set_setting("fsub", {"channel": ""})
        await message.reply_text("✅ <b>Force Subscribe বন্ধ করা হয়েছে!</b>", parse_mode=ParseMode.HTML)
    else:
        await set_setting("fsub", {"channel": val})
        await message.reply_text(f"✅ <b>Force Subscribe সেট হয়েছে:</b> <code>{html.escape(val)}</code>", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("settings") & ADMIN_FILTER & filters.private)
async def cmd_settings(client: Client, message: Message):
    tmdb_k = await get_tmdb_key()
    st_l, st_k = await get_streamtape_creds()
    fsub = await get_setting("fsub")
    fsub_val = (fsub.get("channel") if fsub else None) or config.FORCE_SUB_CHANNEL or "Off"
    await message.reply_text(
        f"⚙️ <b>Current Bot Settings:</b>\n\n"
        f"🎞 <b>TMDB API:</b> {'✅ Connected' if tmdb_k else '❌ Not Set'}\n"
        f"🎬 <b>Streamtape:</b> {f'✅ {html.escape(st_l)}' if st_l else '❌ Not Set'}\n"
        f"📢 <b>Force Sub:</b> <code>{html.escape(fsub_val)}</code>\n"
        f"🌐 <b>App URL:</b> <code>{html.escape(config.APP_URL)}</code>",
        parse_mode=ParseMode.HTML
    )


@Client.on_message(filters.command("stats") & ADMIN_FILTER & filters.private)
async def cmd_stats(client: Client, message: Message):
    if db is None:
        await message.reply_text("❌ Database connected nahi!")
        return
    u_count = await db.users.count_documents({})
    f_count = await db.tg_files.count_documents({})
    p_count = await db.previews.count_documents({})
    await message.reply_text(
        f"📊 <b>Bot Statistics:</b>\n\n"
        f"👥 <b>Total Users:</b> {u_count}\n"
        f"📁 <b>Saved Files:</b> {f_count}\n"
        f"📝 <b>Generated Posts:</b> {p_count}",
        parse_mode=ParseMode.HTML
    )


# ----------------- [ /post ও /player উইজার্ড ] -----------------

@Client.on_message(filters.command("post") & ADMIN_FILTER & filters.private)
async def cmd_new_post(client: Client, message: Message):
    task_id = uuid.uuid4().hex[:8]
    TASKS[task_id] = {
        "embeds": [],
        "downloads": [],
        "labels": [],
        "language": "",
        "is_adult": False,
        "post_mode": "vertical",
        "queue": [],
        "queue_running": False
    }

    tmdb_k = await get_tmdb_key()
    if tmdb_k:
        STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_tmdb"}
        kb = [[{"text": "🔥 Viral / Custom Post (No TMDB)", "callback_data": f"mb_manual|{task_id}", "style": "danger"}]]
        await send_colored_msg(
            message.chat.id,
            "🎬 <b>নতুন পোস্ট তৈরি শুরু হয়েছে!</b>\n\n"
            "• <b>মুভি/সিরিজ হলে:</b> নাম, <b>TMDB লিংক</b>, অথবা <b>IMDb লিংক/ID</b> পাঠান\n"
            "  <i>(যেমন: <code>RRR</code> বা <code>https://www.imdb.com/title/tt8178634/</code>)</i>\n"
            "• <b>ভাইরাল বা কাস্টম ভিডিও হলে:</b> নিচের লাল বাটনে চাপ দিন 👇",
            kb
        )
    else:
        STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_title"}
        await message.reply_text("🎬 <b>ধাপ ১/২: পোস্টের টাইটেল লিখে পাঠান:</b>", parse_mode=ParseMode.HTML)


@Client.on_message(filters.command(["player", "embed"]) & ADMIN_FILTER & filters.private)
async def cmd_player_upload(client: Client, message: Message):
    st_l, st_k = await get_streamtape_creds()
    if not st_l or not st_k:
        await message.reply_text("🔒 আগে <code>/set_player API_LOGIN API_KEY</code> দিয়ে Streamtape সেট করুন।", parse_mode=ParseMode.HTML)
        return

    target = message.reply_to_message
    if not target or not (target.video or target.document):
        await message.reply_text("⚠️ টেলিগ্রামের কোনো ভিডিওতে রিপ্লাই দিয়ে <code>/player</code> দিন, অথবা <code>/post</code> দিয়ে শুরু করুন।", parse_mode=ParseMode.HTML)
        return

    media = target.video or target.document
    fname = getattr(media, "file_name", "video.mp4") or "video.mp4"
    status = await message.reply_text("📥 <b>ভিডিও ডাউনলোড শুরু হচ্ছে...</b>", parse_mode=ParseMode.HTML)

    fpath = None
    try:
        fpath = await download_with_progress(target, status, "📥 ভিডিও ডাউনলোড হচ্ছে...")
        await status.edit_text("☁️ <b>Streamtape-এ আপলোড শুরু হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        fid = await upload_to_streamtape(fpath, fname, st_l, st_k, status_msg=status, label_fn="☁️ Streamtape-এ আপলোড হচ্ছে...")
        if not fid:
            await status.edit_text("❌ Streamtape আপলোড ব্যর্থ হয়েছে!")
            return

        task_id = uuid.uuid4().hex[:8]
        TASKS[task_id] = {
            "title": os.path.splitext(fname)[0],
            "embeds": [{"url": f"https://streamta.pe/e/{fid}"}],
            "downloads": [],
            "labels": [],
            "language": "",
            "is_adult": False,
            "post_mode": "vertical",
            "queue": [],
            "queue_running": False
        }
        await status.delete()
        tmdb_k = await get_tmdb_key()
        if tmdb_k:
            STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_tmdb"}
            kb = [[{"text": "🔥 Viral / Custom Post (No TMDB)", "callback_data": f"mb_manual|{task_id}", "style": "danger"}]]
            await send_colored_msg(
                message.chat.id,
                "✅ <b>Streamtape আপলোড সম্পন্ন!</b>\n\n"
                "🎞 মুভি হলে <b>নাম, TMDB লিংক বা IMDb লিংক</b> পাঠান, আর ভাইরাল/কাস্টম ভিডিও হলে নিচের বাটনে চাপুন:",
                kb
            )
        else:
            STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_title"}
            await message.reply_text("✅ <b>Streamtape আপলোড সম্পন্ন!</b>\n\n📝 এবার পোস্টের টাইটেল লিখে পাঠান:", parse_mode=ParseMode.HTML)
    finally:
        if fpath and os.path.exists(fpath):
            os.remove(fpath)


# ----------------- [ বাটন ও মেসেজ হ্যান্ডলার ] -----------------

@Client.on_callback_query(filters.regex(r"^mb_") & ADMIN_FILTER)
async def admin_callbacks(client: Client, query: CallbackQuery):
    parts = query.data.split("|")
    action = parts[0]
    task_id = parts[1]
    task = TASKS.get(task_id)
    if not task:
        await query.answer("❌ সেশনের মেয়াদ শেষ! আবার /post দিন।", show_alert=True)
        return

    uid = query.from_user.id

    if action == "mb_manual":
        task["post_mode"] = "vertical"
        STATES[uid] = {"task_id": task_id, "step": "waiting_title"}
        await query.message.reply_text("📝 <b>ধাপ ১/২: পোস্টের টাইটেল লিখে পাঠান:</b>", parse_mode=ParseMode.HTML)
        await query.answer()

    elif action == "mb_pick":
        m_type = parts[2]
        m_id = parts[3]
        tmdb_k = await get_tmdb_key()
        await query.answer("Loading movie details...")
        info, err = await fetch_tmdb_details(m_type, m_id, tmdb_k)
        if err:
            await query.message.reply_text(f"❌ {html.escape(err)}", parse_mode=ParseMode.HTML)
            return
        task.update(info)
        task["post_mode"] = "tabs"
        STATES.pop(uid, None)
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))

    elif action == "mb_mode":
        task["post_mode"] = "tabs" if task.get("post_mode", "vertical") == "vertical" else "vertical"
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
        await query.answer(f"Mode: {task['post_mode']}")

    elif action == "mb_back":
        STATES.pop(uid, None)
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
        await query.answer()

    elif action == "mb_langm":
        await edit_colored_msg(
            query.message.chat.id, query.message.id,
            "🗣️ <b>ভাষা (Language) সিলেক্ট করুন বা Custom লিখুন:</b>",
            build_language_keyboard(task_id, task)
        )
        await query.answer()

    elif action == "mb_setl":
        task["language"] = parts[2]
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
        await query.answer(f"Language: {parts[2]}")

    elif action == "mb_clrl":
        task["language"] = ""
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
        await query.answer("Language cleared!")

    elif action == "mb_clang":
        STATES[uid] = {"task_id": task_id, "step": "waiting_custom_lang"}
        await query.message.reply_text("✏️ <b>আপনার পছন্দমতো ভাষার নাম লিখে পাঠান:</b>", parse_mode=ParseMode.HTML)
        await query.answer()

    elif action == "mb_lblm":
        await edit_colored_msg(
            query.message.chat.id, query.message.id,
            "🏷️ <b>ক্যাটাগরি / Labels সিলেক্ট করুন:</b>\n<i>(সবুজ ✅ গুলো পোস্টে যুক্ত হবে, কোনোটিই অটো সিলেক্ট করা নেই)</i>",
            build_category_keyboard(task_id, task)
        )
        await query.answer()

    elif action == "mb_tglbl":
        cat = parts[2]
        lbls = task.setdefault("labels", [])
        if cat in lbls:
            lbls.remove(cat)
        else:
            lbls.append(cat)
        await edit_colored_msg(
            query.message.chat.id, query.message.id,
            "🏷️ <b>ক্যাটাগরি / Labels সিলেক্ট করুন:</b>\n<i>(সবুজ ✅ গুলো পোস্টে যুক্ত হবে)</i>",
            build_category_keyboard(task_id, task)
        )
        await query.answer(f"Updated: {cat}")

    elif action == "mb_clbl":
        STATES[uid] = {"task_id": task_id, "step": "waiting_custom_label"}
        await query.message.reply_text(
            "✏️ <b>ইচ্ছামতো ক্যাটাগরি/লেবেল লিখে পাঠান</b>\n"
            "<i>(একাধিক হলে কমা দিয়ে লিখুন, যেমন: <code>Viral, Bangla, 2026</code>):</i>",
            parse_mode=ParseMode.HTML
        )
        await query.answer()

    elif action == "mb_adult":
        task["is_adult"] = not task.get("is_adult", False)
        await edit_colored_msg(query.message.chat.id, query.message.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
        await query.answer("18+ Updated!")

    elif action == "mb_st":
        st_l, st_k = await get_streamtape_creds()
        if not st_l:
            await query.answer("❌ আগে /set_player দিয়ে Streamtape সেট করুন!", show_alert=True)
            return
        STATES[uid] = {"task_id": task_id, "step": "waiting_batch_videos"}
        kb = [[{"text": "🔙 Back to Draft", "callback_data": f"mb_back|{task_id}", "style": "primary"}]]
        await send_colored_msg(
            query.message.chat.id,
            "📤 <b>বাল্ক ভিডিও আপলোড চালু হয়েছে!</b>\n\n"
            "এখন আপনার <b>সবগুলো ভিডিও (১টা, ৫টা বা ১০টা) একসাথে ফরওয়ার্ড করুন!</b> "
            "বট অটোমেটিক একটার পর একটা আপলোড করে সবগুলো রেডি করে দেবে।",
            kb
        )
        await query.answer()

    elif action == "mb_emb":
        STATES[uid] = {"task_id": task_id, "step": "waiting_embed_url"}
        await query.message.reply_text(
            "🔗 <b>আপনার Embed Link বা iframe কোড পাঠান:</b>\n"
            "<i>(একাধিক লিংক থাকলে প্রতি লাইনে একটি করে লিংক লিখে একসাথে পাঠাতে পারেন!)</i>",
            parse_mode=ParseMode.HTML
        )
        await query.answer()

    elif action == "mb_tg":
        STATES[uid] = {"task_id": task_id, "step": "waiting_tg_file"}
        await query.message.reply_text("📁 <b>ডাউনলোড বাটনের জন্য টেলিগ্রাম ফাইলটি (Video বা Document) ফরওয়ার্ড করুন:</b>", parse_mode=ParseMode.HTML)
        await query.answer()

    elif action == "mb_q":
        quality = parts[2]
        pending = task.pop("pending_file", None)
        if not pending:
            await query.answer("❌ কোনো ফাইল পাওয়া যায়নি!", show_alert=True)
            return
        await save_tg_download(client, query.message.chat.id, task_id, task, pending, quality)
        await query.answer()

    elif action == "mb_cq":
        STATES[uid] = {"task_id": task_id, "step": "waiting_custom_q"}
        await query.message.reply_text("✏️ <b>কাস্টম কোয়ালিটির নাম লিখুন</b> (যেমন: <code>720p HEVC</code> বা <code>Episode 1</code>):", parse_mode=ParseMode.HTML)
        await query.answer()

    elif action == "mb_done":
        if task.get("queue_running"):
            await query.answer("⏳ ভিডিও এখনো আপলোড হচ্ছে! শেষ হওয়া পর্যন্ত অপেক্ষা করুন।", show_alert=True)
            return
        await query.answer("Generating Preview...")
        await generate_final_preview(client, query.message.chat.id, task_id, task)
        TASKS.pop(task_id, None)
        STATES.pop(uid, None)


async def save_tg_download(client: Client, chat_id: int, task_id: str, task: dict, pending: dict, quality: str):
    code = uuid.uuid4().hex[:10]
    bot_user = await get_bot_username(client)
    link = f"https://t.me/{bot_user}?start=dl_{code}"

    if db is not None:
        await db.tg_files.update_one(
            {"_id": code},
            {"$set": {
                "file_id": pending["file_id"],
                "file_name": pending["file_name"],
                "size": pending["size"],
                "quality": quality,
                "chat_id": pending["chat_id"],
                "msg_id": pending["msg_id"],
                "created_at": time.time()
            }},
            upsert=True
        )

    task["downloads"].append({"quality": quality, "size": pending["size"], "link": link})
    await send_colored_msg(
        chat_id,
        f"✅ <b>{html.escape(quality)}</b> যুক্ত হয়েছে!\n\n" + draft_summary_text(task),
        build_draft_keyboard(task_id, task)
    )


@Client.on_message(ADMIN_FILTER & filters.private & (filters.text | filters.photo | filters.video | filters.document), group=2)
async def admin_state_listener(client: Client, message: Message):
    if message.text and message.text.startswith("/"):
        return

    uid = message.from_user.id
    state = STATES.get(uid)
    if not state:
        return

    task_id = state["task_id"]
    task = TASKS.get(task_id)
    if not task:
        STATES.pop(uid, None)
        return

    step = state["step"]

    if step == "waiting_tmdb" and message.text:
        tmdb_k = await get_tmdb_key()
        st = await message.reply_text("🔍 <b>TMDB/IMDb-তে খোঁজা হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        mode, data, err = await search_tmdb(message.text.strip(), tmdb_k)

        if err:
            await st.delete()
            kb = [[{"text": "🔥 Viral / Custom Post (No TMDB)", "callback_data": f"mb_manual|{task_id}", "style": "danger"}]]
            await send_colored_msg(message.chat.id, f"❌ {html.escape(err)}\n\nআবার নাম বা লিংক দিন, অথবা কাস্টম বাটনে চাপুন:", kb)
            return

        if mode == "direct":
            task.update(data)
            task["post_mode"] = "tabs"
            STATES.pop(uid, None)
            await st.delete()
            await send_colored_msg(message.chat.id, draft_summary_text(task), build_draft_keyboard(task_id, task))
            return

        # mode == "choices": একাধিক মুভির সাজেশন বাটন দেখাবে
        await st.delete()
        kb = []
        for c in data:
            icon = "📺" if c["media_type"] == "tv" else "🎬"
            adult_mark = " 🔞" if c["adult"] else ""
            short_t = c["title"][:32] + "..." if len(c["title"]) > 32 else c["title"]
            btn_txt = f"{icon} {short_t} ({c['year']}){adult_mark}"
            kb.append([{
                "text": btn_txt,
                "callback_data": f"mb_pick|{task_id}|{c['media_type']}|{c['id']}",
                "style": "danger" if c["adult"] else "primary"
            }])
        kb.append([{"text": "🔥 None of these (Manual / Viral)", "callback_data": f"mb_manual|{task_id}", "style": "success"}])

        await send_colored_msg(
            message.chat.id,
            "🔎 <b>একাধিক ফলাফল পাওয়া গেছে!</b>\n👇 আপনার সঠিক মুভি/সিরিজটি বাটনে চাপ দিয়ে সিলেক্ট করুন (অথবা সরাসরি নতুন নাম/লিংক পাঠান):",
            kb
        )

    elif step == "waiting_custom_lang" and message.text:
        task["language"] = message.text.strip()
        STATES.pop(uid, None)
        await send_colored_msg(message.chat.id, draft_summary_text(task), build_draft_keyboard(task_id, task))

    elif step == "waiting_custom_label" and message.text:
        new_lbls = [x.strip() for x in message.text.split(",") if x.strip()]
        lbls = task.setdefault("labels", [])
        for x in new_lbls:
            if x not in lbls:
                lbls.append(x)
        STATES.pop(uid, None)
        await send_colored_msg(message.chat.id, draft_summary_text(task), build_draft_keyboard(task_id, task))

    elif step == "waiting_title" and message.text:
        task["title"] = message.text.strip()
        state["step"] = "waiting_poster"
        await message.reply_text("🖼 <b>ধাপ ২/২: পোস্টার (Photo) পাঠান অথবা ছবির URL দিন:</b>", parse_mode=ParseMode.HTML)

    elif step == "waiting_poster" and (message.photo or (message.text and message.text.startswith("http"))):
        if message.photo:
            pid = uuid.uuid4().hex[:8]
            fname = f"{pid}.jpg"
            os.makedirs("downloads/posters", exist_ok=True)
            lpath = os.path.join("downloads/posters", fname)
            await message.download(file_name=lpath)
            if db is not None:
                with open(lpath, "rb") as f:
                    await db.posters.update_one({"_id": pid}, {"$set": {"data": f.read(), "created_at": time.time()}}, upsert=True)
            task["poster_url"] = f"{config.APP_URL}/posters/{fname}"
        else:
            task["poster_url"] = message.text.strip()

        STATES.pop(uid, None)
        await send_colored_msg(message.chat.id, draft_summary_text(task), build_draft_keyboard(task_id, task))

    elif step == "waiting_batch_videos" and (message.video or message.document):
        task.setdefault("queue", []).append(message)
        if not task.get("queue_running"):
            task["queue_running"] = True
            asyncio.create_task(process_batch_queue(client, message.chat.id, uid, task_id))

    elif step == "waiting_embed_url" and message.text:
        lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
        for ln in lines:
            task["embeds"].append({"url": ln})
        STATES.pop(uid, None)
        await send_colored_msg(message.chat.id, draft_summary_text(task), build_draft_keyboard(task_id, task))

    elif step == "waiting_tg_file" and (message.video or message.document):
        media = message.video or message.document
        fname = getattr(media, "file_name", "Movie.mp4") or "Movie.mp4"
        task["pending_file"] = {
            "file_id": media.file_id,
            "file_name": fname,
            "size": humanbytes(media.file_size),
            "chat_id": message.chat.id,
            "msg_id": message.id
        }
        STATES.pop(uid, None)
        qkb = [
            [
                {"text": "🔵 480p", "callback_data": f"mb_q|{task_id}|480p", "style": "primary"},
                {"text": "🟢 720p", "callback_data": f"mb_q|{task_id}|720p", "style": "success"},
                {"text": "🔴 1080p", "callback_data": f"mb_q|{task_id}|1080p", "style": "danger"}
            ],
            [
                {"text": "🟢 4K", "callback_data": f"mb_q|{task_id}|4K", "style": "success"},
                {"text": "🔵 WEB-DL", "callback_data": f"mb_q|{task_id}|WEB-DL", "style": "primary"},
                {"text": "🔴 Custom", "callback_data": f"mb_cq|{task_id}", "style": "danger"}
            ]
        ]
        await send_colored_msg(
            message.chat.id,
            f"📁 ফাইল: <code>{html.escape(fname)}</code> (<code>{humanbytes(media.file_size)}</code>)\n\n👇 <b>কোয়ালিটি সিলেক্ট করুন:</b>",
            qkb
        )

    elif step == "waiting_custom_q" and message.text:
        pending = task.pop("pending_file", None)
        STATES.pop(uid, None)
        if pending:
            await save_tg_download(client, message.chat.id, task_id, task, pending, message.text.strip())
