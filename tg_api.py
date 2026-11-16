# tg_api.py
import aiohttp
import config


def _strip_styles(keyboard: list):
    return [
        [{k: v for k, v in btn.items() if k != "style"} for btn in row]
        for row in keyboard
    ]


async def send_colored_msg(chat_id: int, text: str, keyboard: list = None, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if not data.get("ok") and keyboard:
                payload["reply_markup"] = {"inline_keyboard": _strip_styles(keyboard)}
                async with session.post(url, json=payload) as resp2:
                    return await resp2.json()
            return data


async def edit_colored_msg(chat_id: int, message_id: int, text: str, keyboard: list = None, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if not data.get("ok") and keyboard:
                payload["reply_markup"] = {"inline_keyboard": _strip_styles(keyboard)}
                async with session.post(url, json=payload) as resp2:
                    return await resp2.json()
            return data
