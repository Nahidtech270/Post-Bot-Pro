import os
import re
import html
import json
import time
import uuid
import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import config
from database import db, get_setting, set_setting
from tmdb import fetch_tmdb_data

ADMIN_FILTER = filters.user(config.ADMIN_IDS) if config.ADMIN_IDS else filters.private
TASKS = {}
STATES = {}
_BOT_USERNAME = None

LANGUAGES = ["Hindi Dubbed", "Bangla", "Dual Audio", "South Indian", "English", "Korean"]


def humanbytes(size):
    if not size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.2f} {unit}"


def get_progress_bar(current, total):
    pct = (current / total) * 100 if total > 0 else 0
    filled = int(pct // 10)
    return f"[{'█' * filled}{'░' * (10 - filled)}] {pct:.1f}%"


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


async def upload_to_streamtape(file_path, file_name, api_login, api_key):
    upload_server_url = None
    timeout = aiohttp.ClientTimeout(total=1800)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"https://api.streamtape.com/file/ul?login={api_login}&key={api_key}") as resp:
            data = await resp.json()
            if data.get("status") == 200:
                upload_server_url = data["result"]["url"]
    if not upload_server_url:
        return None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file1", f, filename=file_name)
            async with session.post(upload_server_url, data=form) as resp:
                raw = await resp.text()

    try:
        res = json.loads(raw)
        if str(res.get("status")) == "200":
            return res.get("result", {}).get("id")
    except Exception:
        pass
    m = re.search(r'"id"\s*:\s*"([^"]+)"', raw)
    return m.group(1) if m else None


def build_draft_keyboard(task_id: str, task: dict):
    emb_c = len(task.get("embeds", []))
    dl_c = len(task.get("downloads", []))
    lang = task.get("language", "Hindi Dubbed")
    is_adult = task.get("is_adult", False)
    adult_txt = "🔞 18+ Adult: YES ✅" if is_adult else "🔞 18+ Adult: NO"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🗣️ {lang}", callback_data=f"mb_lang|{task_id}"),
            InlineKeyboardButton(adult_txt, callback_data=f"mb_adult|{task_id}")
        ],
        [InlineKeyboardButton(f"🎬 Add Streamtape Video ({emb_c})", callback_data=f"mb_st|{task_id}")],
        [InlineKeyboardButton(f"🔗 Paste Embed URL / iframe ({emb_c})", callback_data=f"mb_emb|{task_id}")],
        [InlineKeyboardButton(f"📁 Add Telegram File ({dl_c})", callback_data=f"mb_tg|{task_id}")],
        [InlineKeyboardButton("✅ Finish & Get Preview + Copy Code", callback_data=f"mb_done|{task_id}")]
    ])


def draft_summary_text(task: dict):
    return (
        f"🎬 <b>মুভি ড্রাফট রেডি:</b> <code>{html.escape(task.get('title', 'Untitled'))}</code>\n"
        f"⭐ <b>Rating:</b> {task.get('rating', 'N/A')}/10 | 📅 <b>Year:</b> {task.get('year', 'N/A')}\n"
        f"🎭 <b>Genre:</b> {html.escape(task.get('genre', 'Movie'))}\n"
        f"🗣️ <b>Language:</b> {html.escape(task.get('language', 'Hindi Dubbed'))}\n"
        f"▶️ <b>Embed Servers:</b> {len(task.get('embeds', []))} | 📁 <b>Download Files:</b> {len(task.get('downloads', []))}\n\n"
        f"👇 নিচের বাটন থেকে Embed লিংক বা টেলিগ্রাম ডাউনলোড ফাইল যোগ করুন, অথবা <b>Finish</b> চাপুন:"
    )


async def generate_final_preview(client: Client, message: Message, task_id: str, task: dict):
    title = html.escape(task.get("title", "Untitled Movie"))
    poster_url = task.get("poster_url") or "https://via.placeholder.com/300x450/151924/9ca3af?text=No+Poster"
    language = html.escape(task.get("language", "Hindi Dubbed"))
    genre = html.escape(task.get("genre", "Movie"))
    rating = html.escape(str(task.get("rating", "N/A")))
    runtime = html.escape(str(task.get("runtime", "N/A")))
    year = html.escape(str(task.get("year", "N/A")))
    cast = html.escape(task.get("cast", "N/A"))
    storyline = html.escape(task.get("storyline", "Watch or download this movie in HD quality."))
    trailer_id = task.get("trailer_id", "")
    screenshots = task.get("screenshots", [])

    trailer_html = ""
    if trailer_id:
        trailer_html = (
            '<h3>&#127902; Official Trailer</h3>\n'
            '<div class="trailer-box">\n'
            f'  <iframe src="https://www.youtube.com/embed/{html.escape(trailer_id)}" allowfullscreen="allowfullscreen"></iframe>\n'
            '</div>\n'
        )

    ss_html = ""
    if screenshots:
        ss_imgs = "".join([f'<img src="{s}" alt="{title} Screenshot" loading="lazy"/>' for s in screenshots])
        ss_html = f'<h3>&#128248; Screenshots</h3>\n<div class="screenshots-grid">{ss_imgs}</div>\n'

    embed_section_html = ""
    embeds = task.get("embeds", [])
    if embeds:
        tabs_html = ""
        for idx, emb in enumerate(embeds, start=1):
            active_cls = " active" if idx == 1 else ""
            srv_name = html.escape(emb.get("label", f"Server {idx} (HD)"))
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
        f'    <li><strong>Title:</strong> {title}</li>\n'
        f'    <li><strong>Language:</strong> {language}</li>\n'
        f'    <li><strong>Genre:</strong> {genre}</li>\n'
        f'    <li><strong>IMDb Rating:</strong> &#11088; {rating}/10</li>\n'
        f'    <li><strong>Runtime:</strong> {runtime}</li>\n'
        f'    <li><strong>Release Year:</strong> {year}</li>\n'
        f'    <li><strong>Cast:</strong> {cast}</li>\n'
        f'  </ul>\n'
        f'</div>\n\n'
        f'<h3>&#128214; Storyline</h3>\n'
        f'<p>{storyline}</p>\n\n'
        f'{trailer_html}'
        f'{ss_html}'
        f'{dl_box_html}'
    )

    # Labels suggestion
    labels_list = [g.strip() for g in task.get("genre", "").split(",") if g.strip()]
    if task.get("language"):
        labels_list.append(task["language"])
    if downloads:
        q0 = downloads[0]["quality"]
        if q0 in ("HD", "1080p", "4K", "WEB-DL"):
            labels_list.append(q0)
        else:
            labels_list.append("HD")
    if task.get("is_adult"):
        labels_list.append("Adult")
    labels_str = ", ".join(dict.fromkeys(labels_list))

    preview_id = f"p_{task_id}"
    if db is not None:
        await db.previews.update_one(
            {"_id": preview_id},
            {"$set": {
                "title": task.get("title", "Movie"),
                "labels": labels_str,
                "html": blogger_html,
                "created_at": time.time()
            }},
            upsert=True
        )

    preview_url = f"{config.APP_URL}/preview/{preview_id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Live Preview & Copy Code", url=preview_url)]])

    await message.reply_text(
        f"🎉 <b>পোস্ট কোড সফলভাবে তৈরি হয়েছে!</b>\n\n"
        f"🎬 <b>Title:</b> <code>{title}</code>\n"
        f"🏷 <b>Labels:</b> <code>{html.escape(labels_str)}</code>\n\n"
        f"👇 নিচের বাটনে ক্লিক করে লাইভ প্রিভিউ দেখুন এবং ১-ক্লিকে কোড কপি করুন:\n"
        f"🔗 {preview_url}",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


# ----------------- [ সেটিংস ও স্ট্যাটাস কমান্ড ] -----------------

@Client.on_message(filters.command("set_tmdb") & ADMIN_FILTER & filters.private)
async def cmd_set_tmdb(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("⚙️ নিয়ম: `/set_tmdb YOUR_TMDB_API_KEY`")
        return
    await set_setting("tmdb", {"api_key": message.command[1].strip()})
    await message.reply_text("✅ **TMDB API Key সেভ হয়েছে!**")


@Client.on_message(filters.command("set_player") & ADMIN_FILTER & filters.private)
async def cmd_set_player(client: Client, message: Message):
    if len(message.command) < 3:
        await message.reply_text("⚙️ নিয়ম: `/set_player API_LOGIN API_KEY`")
        return
    await set_setting("streamtape", {"login": message.command[1].strip(), "key": message.command[2].strip()})
    await message.reply_text("✅ **Streamtape অ্যাকাউন্ট সেভ হয়েছে!**")


@Client.on_message(filters.command("set_fsub") & ADMIN_FILTER & filters.private)
async def cmd_set_fsub(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("⚙️ নিয়ম:\n• চালু করতে: `/set_fsub @ChannelUsername` বা `-100123456789`\n• বন্ধ করতে: `/set_fsub off`")
        return
    val = message.command[1].strip()
    if val.lower() == "off":
        await set_setting("fsub", {"channel": ""})
        await message.reply_text("✅ **Force Subscribe বন্ধ করা হয়েছে!**")
    else:
        await set_setting("fsub", {"channel": val})
        await message.reply_text(f"✅ **Force Subscribe সেট হয়েছে:** `{val}`\n*(মনে রাখবেন: বটকে ওই চ্যানেলের Admin বানিয়ে রাখবেন)*")


@Client.on_message(filters.command("settings") & ADMIN_FILTER & filters.private)
async def cmd_settings(client: Client, message: Message):
    tmdb_k = await get_tmdb_key()
    st_l, st_k = await get_streamtape_creds()
    fsub = await get_setting("fsub")
    fsub_val = (fsub.get("channel") if fsub else None) or config.FORCE_SUB_CHANNEL or "Off"

    await message.reply_text(
        f"⚙️ <b>Current Bot Settings:</b>\n\n"
        f"🎞 <b>TMDB API:</b> {'✅ Connected' if tmdb_k else '❌ Not Set'}\n"
        f"🎬 <b>Streamtape:</b> {f'✅ {st_l}' if st_l else '❌ Not Set'}\n"
        f"📢 <b>Force Sub:</b> <code>{fsub_val}</code>\n"
        f"🌐 <b>App URL:</b> <code>{config.APP_URL}</code>",
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
    TASKS[task_id] = {"embeds": [], "downloads": [], "language": "Hindi Dubbed", "is_adult": False}

    tmdb_k = await get_tmdb_key()
    if tmdb_k:
        STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_tmdb"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Manual Title & Poster", callback_data=f"mb_manual|{task_id}")]])
        await message.reply_text(
            "🎬 **নতুন মুভি পোস্ট তৈরি**\n\n"
            "🎞 **মুভির নাম বা TMDB ID লিখে পাঠান** (যেমন: `RRR` বা `Pushpa 2` বা `579974`, ওয়েব সিরিজ হলে `tv:1399`):\n"
            "TMDB থেকে অটোমেটিক পোস্টার ও সব ডিটেলস চলে আসবে!",
            reply_markup=kb
        )
    else:
        STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_title"}
        await message.reply_text("🎬 **ধাপ ১/২: মুভির টাইটেল লিখে পাঠান:**")


@Client.on_message(filters.command(["player", "embed"]) & ADMIN_FILTER & filters.private)
async def cmd_player_upload(client: Client, message: Message):
    st_l, st_k = await get_streamtape_creds()
    if not st_l or not st_k:
        await message.reply_text("🔒 আগে `/set_player API_LOGIN API_KEY` দিয়ে Streamtape সেট করুন।")
        return

    target = message.reply_to_message
    if not target or not (target.video or target.document):
        await message.reply_text("⚠️ টেলিগ্রামের কোনো ভিডিওতে রিপ্লাই দিয়ে `/player` দিন, অথবা `/post` দিয়ে শুরু করুন।")
        return

    media = target.video or target.document
    fname = getattr(media, "file_name", "video.mp4") or "video.mp4"
    status = await message.reply_text("📥 <b>ভিডিও ডাউনলোড হচ্ছে...</b>", parse_mode=ParseMode.HTML)

    last_t = 0
    async def prog(cur, tot):
        nonlocal last_t
        now = time.time()
        if now - last_t > 3:
            try:
                await status.edit_text(f"📥 <b>ডাউনলোড:</b>\n{get_progress_bar(cur, tot)}\n💾 {humanbytes(cur)} / {humanbytes(tot)}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
            last_t = now

    fpath = None
    try:
        fpath = await target.download(progress=prog)
        await status.edit_text("☁️ <b>Streamtape-এ আপলোড হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        fid = await upload_to_streamtape(fpath, fname, st_l, st_k)
        if not fid:
            await status.edit_text("❌ Streamtape আপলোড ব্যর্থ হয়েছে!")
            return

        task_id = uuid.uuid4().hex[:8]
        TASKS[task_id] = {
            "title": os.path.splitext(fname)[0],
            "embeds": [{"url": f"https://streamta.pe/e/{fid}", "label": "Server 1 (HD)"}],
            "downloads": [],
            "language": "Hindi Dubbed",
            "is_adult": False
        }
        tmdb_k = await get_tmdb_key()
        if tmdb_k:
            STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_tmdb"}
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Manual Title & Poster", callback_data=f"mb_manual|{task_id}")]])
            await status.edit_text(
                "✅ <b>Streamtape আপলোড সম্পন্ন!</b>\n\n"
                "🎞 এবার <b>মুভির নাম বা TMDB ID</b> পাঠান (অটোমেটিক পোস্টার ও ডিটেলস আনার জন্য):",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        else:
            STATES[message.from_user.id] = {"task_id": task_id, "step": "waiting_title"}
            await status.edit_text("✅ <b>Streamtape আপলোড সম্পন্ন!</b>\n\n📝 এবার পোস্টের টাইটেল লিখে পাঠান:", parse_mode=ParseMode.HTML)
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
        STATES[uid] = {"task_id": task_id, "step": "waiting_title"}
        await query.message.reply_text("📝 **মুভির টাইটেল লিখে পাঠান:**")
        await query.answer()

    elif action == "mb_lang":
        cur = task.get("language", "Hindi Dubbed")
        idx = (LANGUAGES.index(cur) + 1) % len(LANGUAGES) if cur in LANGUAGES else 0
        task["language"] = LANGUAGES[idx]
        await query.message.edit_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)
        await query.answer(f"Language: {task['language']}")

    elif action == "mb_adult":
        task["is_adult"] = not task.get("is_adult", False)
        await query.message.edit_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)
        await query.answer("18+ Updated!")

    elif action == "mb_st":
        st_l, st_k = await get_streamtape_creds()
        if not st_l:
            await query.answer("❌ আগে /set_player দিয়ে Streamtape সেট করুন!", show_alert=True)
            return
        STATES[uid] = {"task_id": task_id, "step": "waiting_st_video"}
        await query.message.reply_text("🎬 **Streamtape-এ আপলোডের জন্য ভিডিও ফাইলটি পাঠান:**")
        await query.answer()

    elif action == "mb_emb":
        STATES[uid] = {"task_id": task_id, "step": "waiting_embed_url"}
        await query.message.reply_text("🔗 **Embed Link (`https://...`) অথবা পুরো `<iframe>` কোডটি পেস্ট করে পাঠান:**")
        await query.answer()

    elif action == "mb_tg":
        STATES[uid] = {"task_id": task_id, "step": "waiting_tg_file"}
        await query.message.reply_text("📁 **ডাউনলোড বাটনের জন্য টেলিগ্রাম ফাইলটি (Video বা Document) ফরওয়ার্ড করুন:**")
        await query.answer()

    elif action == "mb_q":
        quality = parts[2]
        pending = task.pop("pending_file", None)
        if not pending:
            await query.answer("❌ কোনো ফাইল পাওয়া যায়নি!", show_alert=True)
            return
        await save_tg_download(client, query.message, task_id, task, pending, quality)
        await query.answer()

    elif action == "mb_cq":
        STATES[uid] = {"task_id": task_id, "step": "waiting_custom_q"}
        await query.message.reply_text("✏️ **কাস্টম কোয়ালিটির নাম লিখুন** (যেমন: `720p HEVC` বা `Episode 1`):")
        await query.answer()

    elif action == "mb_done":
        await query.answer("Generating Preview...")
        await generate_final_preview(client, query.message, task_id, task)
        TASKS.pop(task_id, None)
        STATES.pop(uid, None)


async def save_tg_download(client: Client, message: Message, task_id: str, task: dict, pending: dict, quality: str):
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
    await message.reply_text(
        f"✅ <b>{html.escape(quality)}</b> যুক্ত হয়েছে!\n\n" + draft_summary_text(task),
        reply_markup=build_draft_keyboard(task_id, task),
        parse_mode=ParseMode.HTML
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
        st = await message.reply_text("🔍 **TMDB থেকে তথ্য আনা হচ্ছে...**")
        info, err = await fetch_tmdb_data(message.text.strip(), tmdb_k)
        if not info:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Manual Title & Poster", callback_data=f"mb_manual|{task_id}")]])
            await st.edit_text(f"❌ {err}\n\nআবার সঠিক নাম দিন অথবা Manual বাটনে চাপুন:", reply_markup=kb)
            return

        task.update(info)
        STATES.pop(uid, None)
        await st.edit_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)

    elif step == "waiting_title" and message.text:
        task["title"] = message.text.strip()
        state["step"] = "waiting_poster"
        await message.reply_text("🖼 **এবার মুভির পোস্টার (Photo) পাঠান অথবা ছবির URL দিন:**")

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
        await message.reply_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)

    elif step == "waiting_st_video" and (message.video or message.document):
        st_l, st_k = await get_streamtape_creds()
        media = message.video or message.document
        fname = getattr(media, "file_name", "video.mp4") or "video.mp4"
        snum = len(task["embeds"]) + 1
        st = await message.reply_text(f"📥 <b>ভিডিও {snum} ডাউনলোড হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        fpath = await message.download()
        await st.edit_text(f"☁️ <b>Streamtape-এ আপলোড হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        fid = await upload_to_streamtape(fpath, fname, st_l, st_k)
        if fpath and os.path.exists(fpath):
            os.remove(fpath)
        if fid:
            task["embeds"].append({"url": f"https://streamta.pe/e/{fid}", "label": f"Server {snum} (HD)"})
            STATES.pop(uid, None)
            await st.edit_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)
        else:
            await st.edit_text("❌ আপলোড ব্যর্থ হয়েছে!")

    elif step == "waiting_embed_url" and message.text:
        snum = len(task["embeds"]) + 1
        task["embeds"].append({"url": message.text.strip(), "label": f"Server {snum} (HD)"})
        STATES.pop(uid, None)
        await message.reply_text(draft_summary_text(task), reply_markup=build_draft_keyboard(task_id, task), parse_mode=ParseMode.HTML)

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
        qkb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("480p", callback_data=f"mb_q|{task_id}|480p"),
                InlineKeyboardButton("720p", callback_data=f"mb_q|{task_id}|720p"),
                InlineKeyboardButton("1080p", callback_data=f"mb_q|{task_id}|1080p")
            ],
            [
                InlineKeyboardButton("4K", callback_data=f"mb_q|{task_id}|4K"),
                InlineKeyboardButton("WEB-DL", callback_data=f"mb_q|{task_id}|WEB-DL"),
                InlineKeyboardButton("✏️ Custom", callback_data=f"mb_cq|{task_id}")
            ]
        ])
        await message.reply_text(f"📁 ফাইল: `{fname}` (`{humanbytes(media.file_size)}`)\n\n👇 **কোয়ালিটি সিলেক্ট করুন:**", reply_markup=qkb)

    elif step == "waiting_custom_q" and message.text:
        pending = task.pop("pending_file", None)
        STATES.pop(uid, None)
        if pending:
            await save_tg_download(client, message, task_id, task, pending, message.text.strip())
