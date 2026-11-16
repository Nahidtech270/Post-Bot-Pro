import re
import aiohttp
from urllib.parse import quote


async def fetch_tmdb_data(query_text: str, api_key: str):
    if not api_key:
        return None, "TMDB API Key সেট করা নেই! `/set_tmdb KEY` দিয়ে সেট করুন।"

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        media_type = "movie"
        tmdb_id = None
        clean_q = query_text.strip()

        if clean_q.lower().startswith("tv:"):
            media_type = "tv"
            clean_q = clean_q[3:].strip()

        if clean_q.isdigit():
            tmdb_id = clean_q
        else:
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", clean_q)
            search_q = re.sub(r"[\(\[]?(19\d{2}|20\d{2})[\)\]]?", "", clean_q).strip() or clean_q

            search_url = f"https://api.themoviedb.org/3/search/multi?api_key={api_key}&query={quote(search_q)}"
            async with session.get(search_url) as resp:
                if resp.status == 401:
                    return None, "TMDB API Key ভুল বা ইনভ্যালিড!"
                if resp.status != 200:
                    return None, f"TMDB সার্চ এরর (Status {resp.status})"
                sdata = await resp.json()
                results = [r for r in sdata.get("results", []) if r.get("media_type") in ("movie", "tv")]
                if not results:
                    return None, f"'{search_q}' নামে TMDB-তে কোনো মুভি বা সিরিজ পাওয়া যায়নি!"

                chosen = results[0]
                if year_match:
                    yr = year_match.group(1)
                    for r in results:
                        rd = r.get("release_date") or r.get("first_air_date") or ""
                        if rd.startswith(yr):
                            chosen = r
                            break

                media_type = chosen.get("media_type", "movie")
                tmdb_id = chosen.get("id")

        detail_url = (
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
            f"?api_key={api_key}&append_to_response=credits,videos,images&include_image_language=en,null"
        )
        async with session.get(detail_url) as resp:
            if resp.status != 200:
                return None, f"TMDB ডিটেলস লোড হয়নি (Status {resp.status})"
            d = await resp.json()

        title = d.get("title") or d.get("name") or clean_q
        rel_date = d.get("release_date") or d.get("first_air_date") or ""
        year = rel_date[:4] if rel_date else "N/A"
        rating = round(float(d.get("vote_average") or 0), 1)
        runtime_val = d.get("runtime") or (d.get("episode_run_time")[0] if d.get("episode_run_time") else 0)
        runtime = f"{runtime_val} min" if runtime_val else "N/A"
        genres = [g["name"] for g in d.get("genres", [])]
        genre_str = ", ".join(genres) if genres else "Drama"
        cast_list = [c["name"] for c in d.get("credits", {}).get("cast", [])[:4]]
        cast = ", ".join(cast_list) or "N/A"
        storyline = d.get("overview") or "No storyline available."

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
            "rating": str(rating),
            "runtime": runtime,
            "genre": genre_str,
            "cast": cast,
            "storyline": storyline,
            "poster_url": poster_url,
            "screenshots": screenshots,
            "trailer_id": trailer_id
        }, None
