import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import time
import html
import aiohttp
from aiohttp import web
from pyrogram import Client, idle
from pyrogram.errors import FloodWait
from pyrogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
import config
from database import db

BOT_STATUS = {
    "state": "Starting...",
    "bot_username": "",
    "wait_until": 0,
    "error": ""
}

routes = web.RouteTableDef()


@routes.get("/")
async def home_route(request):
    return web.Response(text=f"{config.SITE_NAME} Blogger Bot is Running!", content_type="text/plain")


@routes.get("/ping")
async def ping_route(request):
    return web.Response(text="pong", content_type="text/plain")


@routes.get("/status")
async def status_route(request):
    rem = max(0, int(BOT_STATUS["wait_until"] - time.time())) if BOT_STATUS["wait_until"] else 0
    return web.json_response({
        "bot_state": BOT_STATUS["state"],
        "bot_username": BOT_STATUS["bot_username"],
        "floodwait_remaining_seconds": rem,
        "db_connected": db is not None,
        "last_error": BOT_STATUS["error"]
    })


@routes.get("/posters/{filename}")
async def poster_route(request):
    filename = request.match_info["filename"]
    local_path = os.path.join("downloads/posters", filename)
    if os.path.exists(local_path):
        return web.FileResponse(local_path)

    if db is not None:
        pid = os.path.splitext(filename)[0]
        doc = await db.posters.find_one({"_id": pid})
        if doc and doc.get("data"):
            return web.Response(body=bytes(doc["data"]), content_type="image/jpeg")

    return web.Response(status=404, text="Poster not found")


@routes.get("/preview/{preview_id}")
async def preview_route(request):
    pid = request.match_info["preview_id"]
    if db is None:
        return web.Response(status=500, text="Database not connected")

    doc = await db.previews.find_one({"_id": pid})
    if not doc:
        return web.Response(status=404, text="Preview not found or expired")

    raw_html = doc["html"]
    title = html.escape(doc.get("title", "Movie Preview"))
    labels = html.escape(doc.get("labels", ""))

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Preview: {title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet"/>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0b0d13; color: #f3f4f6; font-family: 'Poppins', sans-serif; padding-bottom: 50px; }}
    .top-bar {{
      position: sticky; top: 0; z-index: 99; background: #151924;
      border-bottom: 1px solid #232938; padding: 12px 16px;
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px;
    }}
    .top-bar h1 {{ font-size: 15px; color: #fff; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .btn {{
      border: none; padding: 9px 16px; border-radius: 8px; font-weight: 600;
      font-size: 13px; cursor: pointer; font-family: inherit; color: #fff;
    }}
    .btn-copy {{ background: #e50914; }}
    .btn-lbl {{ background: #2563eb; }}
    .preview-container {{
      max-width: 900px; margin: 22px auto; background: #151924;
      border: 1px solid #232938; border-radius: 12px; padding: 20px;
    }}
    .lbl-box {{
      background: #0b0d13; border: 1px dashed #374151; padding: 10px 14px;
      border-radius: 8px; margin-bottom: 18px; font-size: 13px; color: #9ca3af;
    }}
    .movie-hero-box {{ display: flex; flex-direction: column; gap: 18px; margin-bottom: 20px; background: #0b0d13; padding: 15px; border-radius: 10px; border: 1px solid #232938; }}
    @media (min-width: 640px) {{ .movie-hero-box {{ flex-direction: row; }} }}
    .movie-hero-poster {{ width: 170px; margin: 0 auto; border-radius: 8px; overflow: hidden; flex-shrink: 0; }}
    .movie-hero-poster img {{ width: 100%; display: block; }}
    .movie-info-list {{ flex: 1; list-style: none; font-size: 14px; }}
    .movie-info-list li {{ padding: 6px 0; border-bottom: 1px dashed #232938; }}
    .movie-info-list strong {{ color: #e50914; margin-right: 6px; }}
    h3 {{ font-size: 16px; margin: 16px 0 8px; }}
    p {{ font-size: 14px; color: #9ca3af; line-height: 1.6; }}
    .trailer-box, .embed-player-frame {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; border-radius: 10px; background: #000; margin: 10px 0; }}
    .trailer-box iframe, .embed-player-frame iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0; }}
    .screenshots-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 10px 0; }}
    @media (min-width: 640px) {{ .screenshots-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
    .screenshots-grid img {{ width: 100%; height: 110px; object-fit: cover; border-radius: 6px; }}
    .download-box {{ background: #0b0d13; border: 1px solid #00ff88; border-radius: 12px; padding: 18px; text-align: center; margin-top: 20px; }}
    .vertical-videos-list {{ display: flex; flex-direction: column; gap: 18px; margin-bottom: 18px; text-align: left; }}
    .vertical-video-item {{ background: #151924; padding: 12px; border-radius: 10px; border: 1px solid #232938; }}
    .vertical-video-item h4 {{ font-size: 15px; margin-bottom: 10px; color: #e50914; }}
    .embed-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-bottom: 10px; }}
    .embed-tab-btn {{ background: #151924; color: #fff; border: 1px solid #232938; padding: 7px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
    .embed-tab-btn.active {{ background: #e50914; border-color: #e50914; }}
    .dl-buttons {{ display: flex; flex-direction: column; gap: 10px; max-width: 400px; margin: 0 auto; }}
    .dl-btn {{ display: block; padding: 12px; border-radius: 8px; background: #e50914; color: #fff; text-decoration: none; font-weight: 600; font-size: 14px; }}
    .dl-btn.alt {{ background: #2563eb; }}
    .dl-btn.rgb {{ background: linear-gradient(90deg, #ff0055, #ff8800, #00ccff, #aa00ff); }}
  </style>
</head>
<body>
  <div class="top-bar">
    <h1>🎬 {title}</h1>
    <div class="actions">
      <button class="btn btn-lbl" onclick="copyText('lblSource', this, '✅ Labels Copied!')">🏷 Copy Labels</button>
      <button class="btn btn-copy" onclick="copyText('codeSource', this, '✅ HTML Code Copied!')">📋 Copy Blogger HTML</button>
    </div>
  </div>

  <div class="preview-container">
    <div class="lbl-box"><strong>Suggested Blogger Labels:</strong> {labels or "None"}</div>
    {raw_html}
  </div>

  <textarea id="codeSource" style="display:none;">{html.escape(raw_html)}</textarea>
  <textarea id="lblSource" style="display:none;">{labels}</textarea>

  <script>
    function copyText(id, btn, msg) {{
      var txt = document.getElementById(id).value;
      navigator.clipboard.writeText(txt).then(function() {{
        var old = btn.textContent;
        btn.textContent = msg;
        setTimeout(function() {{ btn.textContent = old; }}, 2000);
      }});
    }}
    function loadThemeEmbed(btn) {{
      var raw = btn.getAttribute('data-embed').trim();
      var sec = btn.closest('.embed-section');
      var box = sec ? sec.querySelector('.embed-player-frame') : document.querySelector('.embed-player-frame');
      if (!box || !raw) return;
      document.querySelectorAll('.embed-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      box.innerHTML = raw.indexOf('<iframe') > -1 ? raw : '<iframe src="' + raw + '" allowfullscreen="true" frameborder="0" scrolling="no"></iframe>';
    }}
    var frames = document.querySelectorAll('.embed-player-frame[data-embed]');
    if (frames.length > 0) {{
      frames.forEach(function(fr) {{
        var raw = fr.getAttribute('data-embed').trim();
        if (!raw.startsWith('http') && raw.indexOf('<iframe') === -1) {{
          try {{ raw = atob(raw); }} catch(e) {{}}
        }}
        fr.innerHTML = raw.indexOf('<iframe') > -1 ? raw : '<iframe src="' + raw + '" loading="lazy" allowfullscreen="true" frameborder="0" scrolling="no"></iframe>';
      }});
    }} else {{
      var first = document.querySelector('.embed-tab-btn');
      if (first) loadThemeEmbed(first);
    }}
  </script>
</body>
</html>"""
    return web.Response(text=page, content_type="text/html")


async def keep_alive_loop():
    await asyncio.sleep(30)
    ping_url = f"{config.APP_URL}/ping"
    timeout = aiohttp.ClientTimeout(total=15)
    while True:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(ping_url) as resp:
                    print(f"💓 Self-Ping ({ping_url}) -> {resp.status}", flush=True)
        except Exception as e:
            print(f"⚠️ Self-Ping error: {e}", flush=True)
        await asyncio.sleep(240)


async def setup_bot_commands(bot: Client):
    await bot.set_bot_commands(
        [BotCommand("start", "Start Bot")],
        scope=BotCommandScopeAllPrivateChats()
    )
    admin_cmds = [
        BotCommand("post", "Create New Post"),
        BotCommand("player", "Upload Video to Streamtape"),
        BotCommand("settings", "View Bot Settings"),
        BotCommand("set_tmdb", "Set TMDB API Key"),
        BotCommand("set_player", "Set Streamtape Account"),
        BotCommand("set_fsub", "Set Force Join Channel"),
        BotCommand("stats", "Bot Statistics")
    ]
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_bot_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass


async def start_services():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    print(f"🌐 Web Server started on port {config.PORT}", flush=True)

    asyncio.create_task(keep_alive_loop())

    bot_token = config.BOT_TOKEN.strip()
    saved_session = None
    token_prefix = bot_token[:15]

    if db is not None:
        try:
            BOT_STATUS["state"] = "Loading session from MongoDB..."
            sdoc = await db.settings.find_one({"_id": "bot_session"})
            if sdoc and sdoc.get("token_hash") == token_prefix:
                saved_session = sdoc.get("session")
        except Exception as e:
            BOT_STATUS["error"] = f"MongoDB error: {e}"
            print(f"⚠️ MongoDB error: {e}", flush=True)

    bot = Client(
        "MovieBloggerBot",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=bot_token,
        session_string=saved_session,
        in_memory=True,
        plugins=dict(root="handlers")
    )

    while True:
        try:
            BOT_STATUS["state"] = "Connecting to Telegram..."
            await bot.start()
            break
        except FloodWait as e:
            wait_sec = int(getattr(e, "value", 60)) + 5
            BOT_STATUS["state"] = "Waiting for Telegram FloodWait"
            BOT_STATUS["wait_until"] = time.time() + wait_sec
            BOT_STATUS["error"] = f"FloodWait {wait_sec}s"
            print(f"⏳ Telegram FloodWait: waiting {wait_sec}s...", flush=True)
            await asyncio.sleep(wait_sec)
            BOT_STATUS["wait_until"] = 0
            BOT_STATUS["error"] = ""
        except Exception as e:
            BOT_STATUS["error"] = str(e)
            print(f"❌ Bot start error: {e}", flush=True)
            if saved_session:
                saved_session = None
                if db is not None:
                    await db.settings.delete_one({"_id": "bot_session"})
                bot = Client(
                    "MovieBloggerBot",
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    bot_token=bot_token,
                    in_memory=True,
                    plugins=dict(root="handlers")
                )
            else:
                await asyncio.sleep(30)

    if not saved_session and db is not None:
        try:
            s_str = await bot.export_session_string()
            await db.settings.update_one(
                {"_id": "bot_session"},
                {"$set": {"session": s_str, "token_hash": token_prefix}},
                upsert=True
            )
            print("💾 Bot session saved to MongoDB!", flush=True)
        except Exception as e:
            print(f"⚠️ Could not save session: {e}", flush=True)

    me = await bot.get_me()
    BOT_STATUS["state"] = "Online"
    BOT_STATUS["bot_username"] = f"@{me.username}"
    BOT_STATUS["error"] = ""

    await setup_bot_commands(bot)
    print(f"🚀 @{me.username} is Online!", flush=True)
    await idle()
    await bot.stop()
    await runner.cleanup()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_services())
