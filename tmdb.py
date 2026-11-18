# tmdb.py
import re
import aiohttp
from urllib.parse import quote


async def fetch_tmdb_details(media_type: str, tmdb_id: str, api_key: str):
    if not api_key:
        return None, "TMDB API Key সেট করা নেই! `/set_tmdb KEY` দিয়ে সেট করুন।"

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        detail_url = (
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
            f"?api_key={api_key}&append_to_response=credits,videos,images&include_image_language=en,null"
        )
        async with session.get(detail_url) as resp:
            if resp.status == 401:
                return None, "TMDB API Key ভুল বা ইনভ্যালিড!"
            if resp.status != 200:
                return None, f"TMDB ডিটেলস লোড হয়নি (Status {resp.status})"
            d = await resp.json()

        title = d.get("title") or d.get("name") or "Untitled"
        rel_date = d.get("release_date") or d.get("first_air_date") or ""
        year = rel_date[:4] if rel_date else "N/A"
        rating = round(float(d.get("vote_average") or 0), 1)
        runtime_val = d.get("runtime") or (d.get("episode_run_time")[0] if d.get("episode_run_time") else 0)
        runtime = f"{runtime_val} min" if runtime_val else "N/A"
        genres = [g["name"] for g in d.get("genres", [])]
        genre_str = ", ".join(genres) if genres else ""
        cast_list = [c["name"] for c in d.get("credits", {}).get("cast", [])[:4]]
        cast = ", ".join(cast_list) or "N/A"
        storyline = d.get("overview") or ""

        poster_path = d.get("poster_path")
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""

        backdrops = d.get("images", {}).get("backdrops", [])[:4]
        screenshots = [f"https://image.tmdb.org/t/p/w780{b['file_path']}" for b in backdrops if b.get("file_path")]
        if not screenshots and d.get("backdrop_path"):
            screenshots = [f"https://image.tmdb.org/t/p/w780{d['backdrop_path']}"]

        trailer_id = ""
        for v in d.get("videos", {}).get("results", []):
            if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
                trailer_id = v.get("key", "")
                break

        return {
            "title": f"{title} ({year})" if year != "N/A" else title,
            "year": year,
            "rating": str(rating) if rating else "N/A",
            "runtime": runtime,
            "genre": genre_str,
            "cast": cast,
            "storyline": storyline,
            "poster_url": poster_url,
            "screenshots": screenshots,
            "trailer_id": trailer_id
        }, None


async def search_tmdb(query_text: str, api_key: str):
    """
    Returns (mode, data, err):
    - mode == "direct": data is full movie dict
    - mode == "choices": data is list of suggestion dicts
    """
    if not api_key:
        return None, None, "TMDB API Key সেট করা নেই! `/set_tmdb KEY` দিয়ে সেট করুন।"

    clean_q = query_text.strip()
    timeout = aiohttp.ClientTimeout(total=20)

    # ১. সরাসরি TMDB Link দিলে (যেমন: https://www.themoviedb.org/movie/579974-rrr)
    tmdb_url_match = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", clean_q, re.I)
    if tmdb_url_match:
        m_type = tmdb_url_match.group(1).lower()
        m_id = tmdb_url_match.group(2)
        info, err = await fetch_tmdb_details(m_type, m_id, api_key)
        if err:
            return None, None, err
        return "direct", info, None

    # ২. সরাসরি IMDb Link বা IMDb ID দিলে (যেমন: https://www.imdb.com/title/tt8178634/ বা tt8178634)
    imdb_match = re.search(r"\b(tt\d{6,10})\b", clean_q, re.I)
    if imdb_match:
        imdb_id = imdb_match.group(1).lower()
        find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={api_key}&external_source=imdb_id"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(find_url) as resp:
                if resp.status == 401:
                    return None, None, "TMDB API Key ভুল বা ইনভ্যালিড!"
                if resp.status != 200:
                    return None, None, f"IMDb খোঁজায় এরর (Status {resp.status})"
                fdata = await resp.json()

        m_res = fdata.get("movie_results", [])
        t_res = fdata.get("tv_results", [])
        if m_res:
            info, err = await fetch_tmdb_details("movie", str(m_res[0]["id"]), api_key)
            return ("direct", info, None) if not err else (None, None, err)
        elif t_res:
            info, err = await fetch_tmdb_details("tv", str(t_res[0]["id"]), api_key)
            return ("direct", info, None) if not err else (None, None, err)
        else:
            return None, None, f"IMDb ID `{imdb_id}` TMDB ডাটাবেজে পাওয়া যায়নি!"

    # ৩. সরাসরি TMDB Numeric ID দিলে (579974 বা tv:1399)
    if clean_q.lower().startswith("tv:") and clean_q[3:].strip().isdigit():
        info, err = await fetch_tmdb_details("tv", clean_q[3:].strip(), api_key)
        return ("direct", info, None) if not err else (None, None, err)

    if clean_q.isdigit():
        info, err = await fetch_tmdb_details("movie", clean_q, api_key)
        return ("direct", info, None) if not err else (None, None, err)

    # ৪. মুভির নাম লিখে সার্চ করলে -> include_adult=true সহ সাজেশন লিস্ট রিটার্ন করবে
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", clean_q)
    search_q = re.sub(r"[\(\[]?(19\d{2}|20\d{2})[\)\]]?", "", clean_q).strip() or clean_q

    search_url = (
        f"https://api.themoviedb.org/3/search/multi"
        f"?api_key={api_key}&query={quote(search_q)}&include_adult=true"
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(search_url) as resp:
            if resp.status == 401:
                return None, None, "TMDB API Key ভুল বা ইনভ্যালিড!"
            if resp.status != 200:
                return None, None, f"TMDB সার্চ এরর (Status {resp.status})"
            sdata = await resp.json()

    results = [r for r in sdata.get("results", []) if r.get("media_type") in ("movie", "tv")]
    if not results:
        return None, None, f"'{search_q}' নামে TMDB-তে কিছু পাওয়া যায়নি! সরাসরি TMDB/IMDb লিংক দিয়ে চেষ্টা করুন।"

    # সাল দেওয়া থাকলে ওই সালের মুভিগুলো সবার উপরে আনবে
    if year_match:
        yr = year_match.group(1)
        results.sort(
            key=lambda r: 0 if (r.get("release_date") or r.get("first_air_date") or "").startswith(yr) else 1
        )

    choices = []
    for r in results[:7]:
        m_type = r.get("media_type", "movie")
        t = r.get("title") or r.get("name") or "Untitled"
        rd = r.get("release_date") or r.get("first_air_date") or ""
        yr = rd[:4] if rd else "N/A"
        choices.append({
            "id": str(r["id"]),
            "media_type": m_type,
            "title": t,
            "year": yr,
            "adult": bool(r.get("adult", False))
        })

    return "choices", choices, None
