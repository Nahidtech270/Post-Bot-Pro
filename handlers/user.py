import html
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import config
from database import db, get_setting


async def auto_delete_task(client: Client, chat_id: int, msg_ids: list, delay: int = 300):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id=chat_id, message_ids=msg_ids)
    except Exception:
        pass


async def get_fsub_channel():
    doc = await get_setting("fsub")
    if doc and doc.get("channel"):
        return doc["channel"]
    return config.FORCE_SUB_CHANNEL or None


async def check_force_sub(client: Client, user_id: int, channel: str):
    if not channel:
        return True, None
    try:
        target = int(channel) if channel.lstrip("-").isdigit() else channel
        member = await client.get_chat_member(target, user_id)
        if member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
            raise UserNotParticipant
        return True, None
    except UserNotParticipant:
        try:
            target = int(channel) if channel.lstrip("-").isdigit() else channel
            chat = await client.get_chat(target)
            invite = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
            if not invite:
                invite = await client.export_chat_invite_link(target)
            return False, invite
        except Exception:
            return True, None
    except Exception:
        return True, None


@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id

    # ইউজার ডাটাবেজে সেভ
    if db is not None:
        await db.users.update_one(
            {"_id": user_id},
            {"$set": {"last_seen": time.time()}},
            upsert=True
        )

    parts = message.text.split(maxsplit=1)

    # ১. ওয়েবসাইট থেকে ফাইল নিতে এলে (/start dl_xxxx)
    if len(parts) == 2 and parts[1].startswith("dl_"):
        code = parts[1].replace("dl_", "", 1).strip()

        # Force Subscribe চেক
        fsub_ch = await get_fsub_channel()
        if fsub_ch:
            joined, invite_link = await check_force_sub(client, user_id, fsub_ch)
            if not joined and invite_link:
                me = await client.get_me()
                retry_url = f"https://t.me/{me.username}?start=dl_{code}"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Official Channel", url=invite_link)],
                    [InlineKeyboardButton("🔄 Try Again (Get File)", url=retry_url)]
                ])
                await message.reply_text(
                    "🔒 <b>Channel Join Required!</b>\n\n"
                    "Please join our official channel first, then click <b>Try Again</b> to get your movie file.",
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML
                )
                return

        if db is None:
            await message.reply_text("❌ Database is currently unavailable.")
            return

        doc = await db.tg_files.find_one({"_id": code})
        if not doc:
            await message.reply_text("❌ Sorry, this file link has expired or does not exist.")
            return

        file_name = doc.get("file_name", "Movie_File")
        quality = doc.get("quality", "HD")
        size = doc.get("size", "")

        caption = (
            f"🎬 <b>{html.escape(file_name)}</b>\n"
            f"📺 <b>Quality:</b> <code>{html.escape(quality)}</code> | 💾 <b>Size:</b> <code>{html.escape(size)}</code>\n\n"
            f"⚠️ <b>WARNING:</b>\n"
            f"This file will be <b>automatically deleted in 5 minutes</b> for copyright protection! "
            f"Please forward it to your <b>Saved Messages</b> immediately.\n"
            f"<i>(ফাইলটি ৫ মিনিট পর ডিলিট হয়ে যাবে, সাথে সাথে অন্য কোথাও ফরওয়ার্ড করে রাখুন!)</i>"
        )

        sent_msg = None
        try:
            sent_msg = await client.send_cached_media(
                chat_id=message.chat.id,
                file_id=doc["file_id"],
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            try:
                sent_msg = await client.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=doc["chat_id"],
                    message_id=doc["msg_id"],
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await message.reply_text(f"❌ Could not deliver file: {e}")
                return

        if sent_msg:
            asyncio.create_task(auto_delete_task(client, message.chat.id, [sent_msg.id], delay=300))
        return

    # ২. সাধারণ /start মেসেজ
    if user_id in config.ADMIN_IDS:
        await message.reply_text(
            f"👋 <b>স্বাগতম অ্যাডমিন! ({config.SITE_NAME})</b>\n\n"
            "📌 <b>অ্যাডমিন কমান্ডসমূহ:</b>\n"
            "• <code>/post</code> — নতুন মুভি পোস্ট তৈরি (TMDB + Embed + Telegram Files)\n"
            "• <code>/player</code> — ভিডিওতে রিপ্লাই দিয়ে Streamtape আপলোড\n"
            "• <code>/set_tmdb KEY</code> — TMDB API Key সেভ\n"
            "• <code>/set_player LOGIN KEY</code> — Streamtape সেভ\n"
            "• <code>/set_fsub @channel</code> — Force Join সেট (অথবা <code>off</code>)\n"
            "• <code>/settings</code> — সব সেটিংস চেক\n"
            "• <code>/stats</code> — বটের স্ট্যাটাস",
            parse_mode=ParseMode.HTML
        )
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🌐 Visit {config.SITE_NAME}", url=config.SITE_URL)]])
        await message.reply_text(
            f"👋 <b>Welcome to {html.escape(config.SITE_NAME)} Official Bot!</b>\n\n"
            "🎬 Browse and download your favorite movies & series directly from our website.",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
