import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9219141073:AAHhE6AWtcHlF0s99Daa_kaJKyelfWeOdNA")
MONGO_URL = os.environ.get("MONGO_URL", "")

# এক বা একাধিক অ্যাডমিন আইডি (কমা দিয়ে দিতে পারেন, যেমন: 12345678,98765432)
_admins = os.environ.get("ADMIN_ID", "")
ADMIN_IDS = [int(x.strip()) for x in _admins.split(",") if x.strip().isdigit()]

# আপনার হোস্টিং ডোমেইন লিংক (যেমন: https://mybot.koyeb.app)
APP_URL = os.environ.get("APP_URL", "http://localhost:8080").strip().rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))

# আপনার ব্লগার ওয়েবসাইটের নাম ও লিংক
SITE_NAME = os.environ.get("SITE_NAME", "MovieHub")
SITE_URL = os.environ.get("SITE_URL", "https://yourwebsite.blogspot.com")

# ঐচ্ছিক: এখান থেকেও দিতে পারেন অথবা বটের /set_tmdb ও /set_fsub কমান্ড দিয়ে সেট করতে পারেন
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "")
