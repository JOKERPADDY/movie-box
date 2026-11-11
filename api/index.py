import os
import time
import json
import requests
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Nexastream / MovieBox Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_TOKEN = os.getenv("API_TOKEN", "")

def verify_token(authorization: str = Header(default="")):
    if API_TOKEN:
        token = authorization.replace("Bearer ", "").strip()
        if token != API_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return True

UPSTREAM_BASE = "https://themoviebox.xyz"
H5_API_BASE = "https://themoviebox.xyz/wefeed-h5api-bff"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": f"{UPSTREAM_BASE}/",
    "X-Request-Lang": "en",
}

session = requests.Session()
session.headers.update(DEFAULT_HEADERS)

_guest_token_cache = {"token": None, "expires": 0}

def get_guest_token() -> str:
    now = time.time()
    if _guest_token_cache["token"] and _guest_token_cache["expires"] > now:
        return _guest_token_cache["token"]
    try:
        resp = session.get(
            f"{H5_API_BASE}/subject/trending",
            params={"page": 1, "perPage": 1, "host": "themoviebox.xyz"},
            timeout=10,
        )
        x_user = resp.headers.get("x-user")
        if x_user:
            token = json.loads(x_user).get("token")
            if token:
                _guest_token_cache["token"] = token
                _guest_token_cache["expires"] = now + 300
                return token
        token = session.cookies.get("token")
        if token:
            _guest_token_cache["token"] = token
            _guest_token_cache["expires"] = now + 300
            return token
    except Exception:
        pass
    return ""

def is_family_friendly(item: dict) -> bool:
    genre_str = (item.get("genre") or "").casefold()
    title_str = (item.get("title") or "").casefold()
    bad_keywords = [
        "adult", "erotica", "savita", "porn", "sex", "orgasm",
        "hentai", "uncensored", "ecchi", "seinen", "mature",
        "melty", "yamitsuki", "kowaremono", "mesudachi", "torokase",
        "inyouchuu", "shiranai", "nagachichi", "kaede", "suzu", "shoku",
        "brickleberry", "kengan ashura", "edition"
    ]
    if any(b in title_str or b in genre_str for b in bad_keywords):
        return False
    if item.get("isPornographic") or item.get("webHighRisk"):
        return False
    return True

@app.get("/")
def root():
    return {"status": "ok", "service": "movie-box API"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/v1/home")
def get_home(_ = Depends(verify_token)):
    try:
        res = session.get(f"{H5_API_BASE}/home", params={"host": "themoviebox.xyz"}, timeout=15)
        if res.status_code == 200:
            data = res.json().get("data", {})
            banners = []
            sections = []
            for item in data.get("operatingList") or []:
                if item.get("type") == "BANNER":
                    banners.extend(_browse_title(x["subject"]) for x in (item.get("banner") or {}).get("items") or [] if x.get("subject"))
                elif item.get("subjects"):
                    sec_items = [_browse_title(x) for x in item["subjects"] if is_family_friendly(x)]
                    if sec_items:
                        sections.append({
                            "title": item.get("title") or "Recommended",
                            "items": sec_items
                        })

            try:
                token = get_guest_token()
                headers = {"Authorization": f"Bearer {token}"} if token else {}

                trend_res = session.post(
                    f"{H5_API_BASE}/subject/search",
                    json={"keyword": "trending", "page": 1, "perPage": 12, "subjectType": 0},
                    headers=headers,
                    timeout=8,
                )
                if trend_res.status_code == 200:
                    trend_items = [_browse_title(x) for x in trend_res.json().get("data", {}).get("items", []) if is_family_friendly(x)]
                    if trend_items:
                        sections.insert(0, {"title": "Trending Today", "items": trend_items})

                kenyan_res = session.post(
                    f"{H5_API_BASE}/subject/search",
                    json={"keyword": "kenyan", "page": 1, "perPage": 12, "subjectType": 0},
                    headers=headers,
                    timeout=8,
                )
                if kenyan_res.status_code == 200:
                    kenyan_items = [_browse_title(x) for x in kenyan_res.json().get("data", {}).get("items", []) if is_family_friendly(x)]
                    if kenyan_items:
                        sections.insert(1 if len(sections) > 0 else 0, {"title": "Kenyan Movies & Series", "items": kenyan_items})
            except Exception:
                pass

            return {"banners": banners, "sections": sections}
    except Exception:
        pass
    return {"banners": [], "sections": []}

def _browse_title(raw: dict) -> dict:
    cover = raw.get("cover") or {}
    stills = raw.get("stills") or {}
    sid = str(raw.get("subjectId") or raw.get("id") or "")
    path = raw.get("detailPath") or raw.get("detail_path") or ""
    return {
        "id": sid,
        "detail_path": path,
        "kind": {1: "movie", 2: "series", 7: "short_series"}.get(raw.get("subjectType"), raw.get("kind", "other")),
        "title": raw.get("title") or "",
        "description": raw.get("description") or "",
        "release_date": raw.get("releaseDate") or None,
        "duration_seconds": raw.get("duration") or None,
        "genres": [value.strip() for value in (raw.get("genre") or "").split(",") if value.strip()],
        "poster_url": cover.get("url") or raw.get("poster_url"),
        "backdrop_url": stills.get("url") or raw.get("backdrop_url"),
        "country": raw.get("countryName") or None,
        "rating": raw.get("imdbRatingValue") or raw.get("rating"),
        "has_resource": bool(raw.get("hasResource") or raw.get("has_resource")),
        "subjectId": sid,
        "detailPath": path,
        "cover": cover,
        "stills": stills,
    }

def _fetch_row_items(args):
    title, query, sub_type, page, headers, target_kind = args
    try:
        req_headers = dict(session.headers)
        if headers:
            req_headers.update(headers)
        res = session.post(
            f"{H5_API_BASE}/subject/search",
            json={"keyword": query, "page": page, "perPage": 12, "subjectType": 0},
            headers=req_headers,
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json().get("data", {})
            sec_items = []
            for x in data.get("items", []):
                if is_family_friendly(x):
                    formatted = _browse_title(x)
                    if target_kind and formatted.get("kind") != target_kind:
                        continue
                    sec_items.append(formatted)
            if sec_items:
                return (title, sec_items)
    except Exception:
        pass
    return None

@app.get("/v1/catalog/{name}")
@app.get("/v1/collections/{name}")
def get_catalog(name: str, page: int = 1, genre: str = Query(default=""), _ = Depends(verify_token)):
    try:
        canonical = name.lower().strip()
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        target_kind = None
        if canonical in ("movies", "movie"):
            target_kind = "movie"
            row_queries = [
                ("All", "trending movie", 0),
                ("Teen Romance", "teen romance movie", 0),
                ("Latest Movies", "latest movies", 0),
                ("All Cinema", "cinema movie", 0),
                ("Netflix Movies", "netflix movie", 0),
                ("South Africa", "south african movie", 0),
                ("Disney+ Movies", "disney plus movie", 0),
                ("Paramount+ Movies", "paramount plus movie", 0),
                ("Action", "action movie", 0),
                ("Crime", "crime movie", 0),
                ("Drama", "drama movie", 0),
                ("Adventure", "adventure movie", 0),
                ("Comedy", "comedy movie", 0),
                ("Thriller", "thriller movie", 0),
                ("Romance", "romance movie", 0),
                ("Sci-Fi", "sci-fi movie", 0),
                ("Documentary", "documentary movie", 0),
                ("Horror", "horror movie", 0),
                ("Fantasy", "fantasy movie", 0),
                ("Family", "family movie", 0),
                ("History", "history movie", 0),
                ("Mystery", "mystery movie", 0),
                ("War", "war movie", 0),
                ("Biography", "biography movie", 0),
                ("Western", "western movie", 0),
            ]
        elif canonical in ("series", "tv", "tvshows"):
            target_kind = "series"
            row_queries = [
                ("New Season & Episode", "new season", 0),
                ("Teen Romance", "teen romance series", 0),
                ("South Africa", "south african series", 0),
                ("NETFLIX", "netflix series", 0),
                ("Prime Video", "prime video series", 0),
                ("Disney+", "disney plus series", 0),
                ("MAX", "hbo max series", 0),
                ("Paramount+", "paramount plus series", 0),
                ("Apple TV", "apple tv series", 0),
                ("Hulu", "hulu series", 0),
                ("Action", "action series", 0),
                ("Adventure", "adventure series", 0),
                ("Comedy", "comedy series", 0),
                ("Crime", "crime series", 0),
                ("Documentary", "documentary series", 0),
                ("Drama", "drama series", 0),
                ("Family", "family series", 0),
                ("Fantasy", "fantasy series", 0),
                ("History", "history series", 0),
                ("Horror", "horror series", 0),
                ("Mystery", "mystery series", 0),
                ("Romance", "romance series", 0),
                ("Thriller", "thriller series", 0),
                ("War", "war series", 0),
                ("Biography", "biography series", 0),
                ("Sci-Fi", "sci-fi series", 0),
                ("Reality TV", "reality tv", 0),
                ("Sport", "sports series", 0),
                ("Musical", "musical series", 0),
                ("Western", "western series", 0),
                ("All", "trending series", 0),
            ]
        elif canonical == "kids":
            target_kind = None
            row_queries = [
                ("All", "kids animation", 0),
                ("Paw Patrol", "paw patrol", 0),
                ("Cartoon Movies", "cartoon movie", 0),
                ("Cartoon Series", "cartoon series", 0),
                ("Baby", "baby cartoon", 0),
                ("Age 2-6", "toddler kids", 0),
                ("Pixar", "pixar", 0),
                ("DreamWorks", "dreamworks", 0),
                ("Blue Sky Studios", "ice age blue sky", 0),
                ("Illumination", "illumination animation", 0),
                ("Disney's Full-length Animated Classics", "disney animation", 0),
                ("Toys", "toy story", 0),
                ("Kung Fu Panda", "kung fu panda", 0),
                ("Cars", "cars animation", 0),
                ("Frozen", "frozen disney", 0),
                ("Minion", "minions", 0),
                ("Peppa Pig", "peppa pig", 0),
                ("Akili Kids", "akili kids", 0),
            ]
        elif canonical == "anime":
            target_kind = None
            row_queries = [
                ("All Anime", "anime series", 0),
                ("Anime Movies", "anime movie", 0),
                ("Japanese Anime", "japanese anime", 0),
                ("European and American Anime", "western animation", 0),
                ("Age 7-12", "kids anime", 0),
                ("Dragon Ball", "dragon ball", 0),
                ("Naruto", "naruto", 0),
                ("One Piece", "one piece", 0),
            ]
        else:
            keyword = canonical if canonical != "animation" else "animation"
            row_queries = [(name.capitalize(), keyword, 0)]

        tasks = [(title, query, sub_type, page, headers, target_kind) for title, query, sub_type in row_queries]

        sections = []
        all_items = []
        seen_ids = set()

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(_fetch_row_items, tasks))

        for res in results:
            if res:
                title, sec_items = res
                sections.append({"title": title, "items": sec_items})
                for item in sec_items:
                    sid = item.get("id")
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        all_items.append(item)

        return {
            "banners": [],
            "sections": sections
        }
    except Exception as e:
        return {"error": str(e), "banners": [], "sections": []}

@app.get("/v1/search")
def get_search(q: str = Query(...), page: int = 1, _ = Depends(verify_token)):
    try:
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = session.post(
            f"{H5_API_BASE}/subject/search",
            json={"keyword": q, "page": page, "perPage": 28, "subjectType": 0},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            data = res.json().get("data", {})
            items = [x for x in data.get("items", []) if is_family_friendly(x)]
            return {"items": items, "next_page": data.get("next_page")}
    except Exception:
        pass
    return {"items": [], "next_page": None}

@app.get("/v1/search/suggestions")
def get_suggestions(q: str = Query(...), _ = Depends(verify_token)):
    try:
        token = get_guest_token()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = session.post(
            f"{H5_API_BASE}/subject/search-suggest",
            json={"keyword": q, "perPage": 10},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            items = res.json().get("data", {}).get("items", [])
            return [x["word"] for x in items if x.get("word")]
    except Exception:
        pass
    return []

@app.get("/v1/search/popular")
def get_popular(_ = Depends(verify_token)):
    try:
        res = session.get(
            f"{H5_API_BASE}/subject/everyone-search",
            params={"host": "themoviebox.xyz"},
            timeout=15,
        )
        if res.status_code == 200:
            items = res.json().get("data", {}).get("everyoneSearch", [])
            return [x["title"] for x in items if x.get("title")]
    except Exception:
        pass
    return []

@app.get("/v1/titles/{path:path}/recommendations")
def get_recommendations(path: str, page: int = 1, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        if detail_res.status_code == 200:
            subject_id = detail_res.json().get("data", {}).get("subject", {}).get("subjectId")
            if subject_id:
                rec_res = session.get(
                    f"{H5_API_BASE}/subject/detail-rec",
                    params={"subjectId": subject_id, "page": page, "perPage": 12, "host": "themoviebox.xyz"},
                    headers=headers,
                    timeout=15,
                )
                if rec_res.status_code == 200:
                    items = [x for x in rec_res.json().get("data", {}).get("items", []) if is_family_friendly(x)]
                    return {"items": items, "next_page": page + 1 if len(items) == 12 else None}
    except Exception:
        pass
    return {"items": [], "next_page": None}

@app.get("/v1/titles/{path:path}/playback")
def get_playback(path: str, season: int = 0, episode: int = 0, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        subject_id = None
        if detail_res.status_code == 200:
            subject_data = detail_res.json().get("data", {})
            subject_id = subject_data.get("subject", {}).get("subjectId")

        play_res = session.get(
            f"{H5_API_BASE}/subject/play",
            params={
                "subjectId": subject_id or "",
                "se": season,
                "ep": episode,
                "detailPath": path,
                "streamSignType": 1,
                "supportCodecs[h264]": 1,
            },
            headers=headers,
            timeout=15,
        )
        if play_res.status_code == 200:
            return play_res.json().get("data", {"streams": [], "vip_locked": False, "limited": False})
        raise HTTPException(status_code=play_res.status_code, detail="Playback unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/titles/{path:path}/captions")
def get_captions(path: str, stream_id: str = "", season: int = 0, episode: int = 0, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        detail_res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        subject_id = ""
        if detail_res.status_code == 200:
            subject_id = detail_res.json().get("data", {}).get("subject", {}).get("subjectId", "")

        cap_res = session.get(
            f"{H5_API_BASE}/subject/caption",
            params={
                "format": "MP4",
                "id": stream_id,
                "subjectId": subject_id,
                "detailPath": path,
            },
            headers=headers,
            timeout=15,
        )
        if cap_res.status_code == 200:
            captions_raw = cap_res.json().get("data", {}).get("captions", [])
            formatted = []
            for c in captions_raw:
                lang = c.get("lan", "en").replace("in_id", "id")
                formatted.append({
                    "label": c.get("lanName", lang),
                    "language": lang,
                    "url": c.get("url"),
                    "request_headers": {"Referer": UPSTREAM_BASE, "User-Agent": DEFAULT_HEADERS["User-Agent"]}
                })
            return formatted
    except Exception:
        pass
    return []

@app.get("/v1/titles/{path:path}")
def get_title_detail(path: str, _ = Depends(verify_token)):
    try:
        headers = {"Referer": f"{UPSTREAM_BASE}/movies/{path}"}
        res = session.get(
            f"{H5_API_BASE}/detail",
            params={"detailPath": path, "host": "themoviebox.xyz"},
            headers=headers,
            timeout=15,
        )
        if res.status_code == 200:
            payload = res.json()
            data = payload.get("data")
            if data and ("subject" in data or "title" in data):
                return data
            if payload.get("code") == 0:
                return data or {}
            raise HTTPException(status_code=404, detail=f"Upstream returned code {payload.get('code')}: {payload.get('msg')}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Title not found")
