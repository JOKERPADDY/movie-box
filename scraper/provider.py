import json
import os
import re
import threading
import time
import base64
import hashlib
import hmac
from urllib.parse import quote, urlparse
import requests
import httpx

try:
    from .models import Caption, CatalogFilters, Detail, Dub, Episode, FilterGroup, FilterValue, Home, Page, Playback, Ranking, Season, Section, Stream, Title
except ImportError:
    from models import Caption, CatalogFilters, Detail, Dub, Episode, FilterGroup, FilterValue, Home, Page, Playback, Ranking, Season, Section, Stream, Title

BASE_URL = "https://themoviebox.xyz"
API_PATH = "/wefeed-h5api-bff"
OFFICIAL_BASE_URL = "https://api.inmoviebox.com"
OFFICIAL_KEY_STR = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"


class ProviderError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def md5_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()


def _search_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _kind(value: int | None) -> str:
    return {1: "movie", 2: "series", 7: "short_series"}.get(value, "other")


def _title(raw: dict) -> Title:
    cover = raw.get("cover") or {}
    stills = raw.get("stills") or {}
    return Title(
        id=str(raw.get("subjectId") or ""),
        detail_path=raw.get("detailPath") or "",
        kind=_kind(raw.get("subjectType")),
        title=raw.get("title") or "",
        description=raw.get("description") or "",
        release_date=raw.get("releaseDate") or None,
        duration_seconds=raw.get("duration") or None,
        genres=[v.strip() for v in (raw.get("genre") or "").split(",") if v.strip()],
        poster_url=cover.get("url"),
        backdrop_url=stills.get("url"),
        country=raw.get("countryName") or None,
        rating=raw.get("imdbRatingValue") or None,
        has_resource=bool(raw.get("hasResource")),
    )


def _sections(data: dict) -> Home:
    banners = []
    sections = []
    for item in data.get("operatingList") or []:
        if item.get("type") == "BANNER":
            banners.extend(_title(x["subject"]) for x in (item.get("banner") or {}).get("items") or [] if x.get("subject"))
        elif item.get("subjects"):
            sections.append(Section(title=item.get("title") or "", kind=item.get("type") or "", items=[_title(x) for x in item["subjects"]]))
    return Home(banners=banners, sections=sections)


def _number(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def decode_sign_cookie_url(cookie_str: str) -> str | None:
    if not cookie_str or "urlprefix=" not in cookie_str:
        return None
    try:
        prefix_start = cookie_str.find("urlprefix=") + len("urlprefix=")
        prefix_end = cookie_str.find(":", prefix_start)
        if prefix_end == -1:
            prefix_end = cookie_str.find(";", prefix_start)
        if prefix_end == -1:
            prefix_end = len(cookie_str)
        base64_str = cookie_str[prefix_start:prefix_end]
        padding = (4 - len(base64_str) % 4) % 4
        base64_str += "=" * padding
        decoded_bytes = base64.b64decode(base64_str)
        decoded_path = decoded_bytes.decode("utf-8")
        if decoded_path.endswith("/"):
            return f"{decoded_path}index.m3u8"
        else:
            return f"{decoded_path}/index.m3u8"
    except Exception:
        return None


def is_dummy_url(url: str) -> bool:
    if not url:
        return True
    u_lower = url.lower()
    return any(dummy in u_lower for dummy in ["macdn.aoneroom.com", "please_update", "update_app", "b164fbfb43"])


_visitor_token_cache = {"token": None, "expires": 0}


def get_official_signature(method: str, endpoint: str, query_params: dict, body_str: str, timestamp_str: str) -> tuple[str, str]:
    reversed_ts = timestamp_str[::-1]
    client_token = f"{timestamp_str},{md5_hex(reversed_ts)}"
    params_copy = dict(query_params)
    if "host" not in params_copy:
        params_copy["host"] = "api.inmoviebox.com"
    sorted_keys = sorted(params_copy.keys())
    query_parts = [f"{k}={params_copy[k]}" for k in sorted_keys]
    sorted_query_str = "&".join(query_parts)
    canonical_path_query = endpoint + (f"?{sorted_query_str}" if sorted_query_str else "")
    body_length = ""
    body_hash = ""
    if body_str:
        body_bytes = body_str.encode("utf-8")
        body_length = str(len(body_bytes))
        sub_bytes = body_bytes[:102400]
        body_hash = md5_hex(sub_bytes)
    accept = "application/json"
    content_type = "application/json;charset=UTF-8"
    canonical_str = f"{method}\n{accept}\n{content_type}\n{body_length}\n{timestamp_str}\n{body_hash}\n{canonical_path_query}"
    try:
        key_bytes = base64.b64decode(OFFICIAL_KEY_STR)
    except Exception:
        key_bytes = OFFICIAL_KEY_STR.encode("utf-8")
    sig_digest = hmac.new(key_bytes, canonical_str.encode("utf-8"), hashlib.md5).digest()
    base64_sig = base64.b64encode(sig_digest).decode("utf-8")
    return client_token, f"{timestamp_str}|2|{base64_sig}"


def make_official_request(method: str, endpoint: str, query_params: dict = None, body_str: str = "") -> dict | None:
    global _visitor_token_cache
    if query_params is None:
        query_params = {}
    timestamp_str = str(int(time.time() * 1000))
    client_token, signature_header = get_official_signature(method, endpoint, query_params, body_str, timestamp_str)
    now = time.time()
    active_token = _visitor_token_cache.get("token")
    if not active_token or _visitor_token_cache.get("expires", 0) <= now:
        if endpoint != "/wefeed-mobile-bff/user-api/visitor-login":
            res = make_official_request("POST", "/wefeed-mobile-bff/user-api/visitor-login", body_str="{}")
            if res and isinstance(res.get("data"), dict):
                active_token = res["data"].get("token")
                if active_token:
                    _visitor_token_cache["token"] = active_token
                    _visitor_token_cache["expires"] = now + 3600

    headers = {
        "User-Agent": "MovieBox/4.0.02 (Android 14; Pixel 6)",
        "Accept": "application/json",
        "Accept-Language": "en-IN,hi-IN;q=0.9,en;q=0.8",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Sign-Version": "2.0",
        "appid": "4U01pxRu278GqCZKY9",
        "region": "IN",
        "lang": "en",
        "os": "android",
        "X-Timestamp": timestamp_str,
        "Referer": "https://api.inmoviebox.com/",
        "X-Client-Token": client_token,
        "x-tr-signature": signature_header,
        "X-Play-Mode": "2",
        "X-Client-Info": json.dumps({
            "package_name": "com.community.oneroom",
            "version_name": "4.0.02",
            "version_code": 50020126,
            "os": "android",
            "os_version": "14",
            "install_ch": "ps",
            "device_id": "a1b2c3d4e5f67890",
            "install_store": "ps",
            "gaid": "",
            "brand": "Google",
            "model": "Pixel 6",
            "system_language": "en",
            "net": "wifi",
            "region": "IN",
            "timezone": "Asia/Kolkata",
            "sp_code": "404"
        }, separators=(',', ':'))
    }
    if active_token:
        headers["Authorization"] = f"Bearer {active_token}"
        headers["X-Client-Status"] = "0"
    else:
        headers["X-Client-Status"] = "1"

    url_params = dict(query_params)
    if "host" not in url_params:
        url_params["host"] = "api.inmoviebox.com"
    url = f"{OFFICIAL_BASE_URL}{endpoint}"
    try:
        if method.upper() == "GET":
            resp = requests.get(url, params=url_params, headers=headers, timeout=15)
        else:
            resp = requests.post(url, params=url_params, data=body_str, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


class MovieBoxProvider:
    def __init__(self, client: httpx.Client | None = None, cache_seconds: int = 120):
        proxy = os.getenv("SCRAPER_UPSTREAM_PROXY") or None
        self.client = client or httpx.Client(
            base_url=BASE_URL,
            timeout=20,
            follow_redirects=True,
            proxy=proxy,
        )
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, object]] = {}
        self._subjects: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.headers = {"Referer": f"{BASE_URL}/", "User-Agent": "Mozilla/5.0", "X-Request-Lang": "en"}

    def close(self):
        self.client.close()

    def _request(self, method: str, path: str, *, params=None, body=None, referer=None, extra_headers=None) -> httpx.Response:
        for attempt in range(3):
            try:
                response = self.client.request(method, path, params=params, json=body, headers={**self.headers, **({"Referer": referer} if referer else {}), **(extra_headers or {})})
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise ProviderError("Provider connection failed") from exc
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.status_code == 404:
                raise ProviderError("Title not found", 404)
            if response.status_code >= 400:
                raise ProviderError(f"Provider returned HTTP {response.status_code}")
            return response
        raise ProviderError("Provider unavailable")

    def _data(self, path: str, *, params=None, body=None, referer=None, extra_headers=None) -> dict:
        response = self._request("POST" if body is not None else "GET", API_PATH + path, params=params, body=body, referer=referer, extra_headers=extra_headers)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Provider returned invalid JSON") from exc
        if payload.get("code") != 0:
            raise ProviderError("Provider rejected the request")
        return payload.get("data") or {}

    def _cached(self, key: str, fetch):
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
        value = fetch()
        with self._lock:
            self._cache[key] = (time.monotonic() + self.cache_seconds, value)
        return value

    def home(self) -> Home:
        return self._cached("home", lambda: _sections(self._data("/home", params={"host": "themoviebox.xyz"})))

    def collection(self, name: str) -> Home:
        tabs = {"movies": 2, "midnight": 9}
        if name not in tabs:
            raise ProviderError("Collection not found", 404)
        return self._cached(f"collection:{name}", lambda: _sections(self._data("/tab-operating", params={"tabId": tabs[name], "host": "themoviebox.xyz"})))

    def browse(self, page: int = 1, kind: str | None = None, tab: str | None = None) -> Page:
        if kind == "short_series":
            data = self._cached(
                f"browse:short_series:{page}",
                lambda: self._data("/subject/filter", body={"channelId": 7, "page": page, "perPage": 18, "sort": "ForYou"}),
            )
            pager = data.get("pager") or {}
            items = [_title(x) for x in data.get("items") or []]
            return Page(
                items=[item for item in items if item.kind == "short_series"],
                page=page,
                next_page=_number(pager.get("nextPage")) if pager.get("hasMore") else None,
                has_more=bool(pager.get("hasMore")),
            )
        selected_tab = tab or ("movies" if kind == "movie" else "home")
        tab_id = {"home": None, "movies": 2, "midnight": 9}.get(selected_tab)
        params = {"page": page, "perPage": 18}
        if tab_id is not None:
            params["tabId"] = tab_id
        data = self._cached(f"browse:{selected_tab}:{page}", lambda: self._data("/subject/trending", params=params))
        items = [_title(x) for x in data.get("subjectList") or []]
        if kind:
            items = [item for item in items if item.kind == kind]
        pager = data.get("pager") or {}
        return Page(
            items=items,
            page=page,
            next_page=_number(pager.get("nextPage")) if pager.get("hasMore") else None,
            has_more=bool(pager.get("hasMore")),
        )

    def catalog_filters(self, name: str) -> CatalogFilters:
        return CatalogFilters(groups=[])

    def catalog(self, name: str, page: int = 1, **kwargs) -> Page:
        data = self._cached(f"catalog:{name}:{page}", lambda: self._data("/subject/search", body={"keyword": name, "page": page, "perPage": 18, "subjectType": 0}))
        items = [_title(x) for x in data.get("items") or []]
        return Page(items=items, page=page, next_page=page + 1 if len(items) == 18 else None, has_more=len(items) == 18)

    def rankings(self) -> list[Ranking]:
        return [Ranking(id="1", name="Popular Movies"), Ranking(id="5", name="Trending Series")]

    def ranking(self, ranking_id: str, page: int = 1) -> Page:
        res = make_official_request("GET", "/wefeed-mobile-bff/tab-operating", {"tabId": str(ranking_id), "page": str(page), "pageSize": "24"})
        items = []
        if res and res.get("data"):
            items = [_title(x) for x in res["data"].get("items") or res["data"].get("subjectList") or []]
        return Page(items=items, page=page, next_page=page + 1 if len(items) == 24 else None, has_more=len(items) == 24)

    def search(self, q: str, page: int = 1) -> Page:
        data = self._data("/subject/search", body={"keyword": q, "page": page, "perPage": 18, "subjectType": 0})
        items = [_title(x) for x in data.get("items") or []]
        return Page(items=items, page=page, next_page=page + 1 if len(items) == 18 else None, has_more=len(items) == 18)

    def suggestions(self, q: str) -> list[str]:
        data = self._data("/subject/search-suggest", body={"keyword": q, "perPage": 10})
        return [x["word"] for x in data.get("items") or [] if x.get("word")]

    def popular_searches(self) -> list[str]:
        data = self._data("/subject/everyone-search", params={"host": "themoviebox.xyz"})
        return [x["title"] for x in data.get("everyoneSearch") or [] if x.get("title")]

    def detail(self, detail_path: str) -> Detail:
        if detail_path.isdigit():
            res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get", {"subjectId": detail_path})
            if res and res.get("data"):
                raw = res["data"]
                return Detail(
                    title=_title(raw),
                    seasons=[Season(number=1, episode_count=raw.get("episodeCount") or 1, resolutions=[720, 1080])],
                    dubs=[]
                )
        try:
            data = self._data("/detail", params={"detailPath": detail_path, "host": "themoviebox.xyz"}, referer=f"{BASE_URL}/movies/{detail_path}")
            subject = data.get("subject") or {}
            seasons = [Season(number=s.get("se") or 1, episode_count=s.get("epNum") or 1, resolutions=[720, 1080]) for s in data.get("seasons") or []]
            return Detail(title=_title(subject), seasons=seasons, dubs=[])
        except Exception:
            clean_q = re.sub(r"-\d{4}$", "", detail_path).replace("-", " ").strip()
            res = make_official_request("POST", "/wefeed-mobile-bff/subject-api/search", {}, json.dumps({"keyword": clean_q, "page": 1, "perPage": 5, "subjectType": 0}))
            items = (res.get("data") or {}).get("items") or [] if res else []
            if items:
                raw = items[0]
                return Detail(
                    title=_title(raw),
                    seasons=[Season(number=1, episode_count=raw.get("episodeCount") or 1, resolutions=[720, 1080])],
                    dubs=[]
                )
            raise ProviderError("Title not found", 404)

    def episodes(self, detail_path: str, season: int) -> list[Episode]:
        detail = self.detail(detail_path)
        selected = next((x for x in detail.seasons if x.number == season), None)
        ep_count = selected.episode_count if selected else 1
        return [Episode(season=season, number=ep) for ep in range(1, ep_count + 1)]

    def recommendations(self, detail_path: str, page: int = 1) -> Page:
        detail = self.detail(detail_path)
        data = self._data("/subject/detail-rec", params={"subjectId": detail.title.id, "page": page, "perPage": 12}, referer=f"{BASE_URL}/movies/{detail_path}")
        items = [_title(x) for x in data.get("items") or []]
        return Page(items=items, page=page, next_page=page + 1 if len(items) == 12 else None, has_more=len(items) == 12)

    def playback(self, detail_path: str, season: int = 0, episode: int = 0) -> Playback:
        sid = detail_path
        if not sid.isdigit():
            try:
                detail = self.detail(detail_path)
                sid = detail.title.id
            except Exception:
                pass

        s_num = max(1, season)
        e_num = max(1, episode)

        play_res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/play-info/v2", {"subjectId": sid, "se": str(s_num), "ep": str(e_num), "host": "api6.aoneroom.com"})
        play_data = (play_res.get("data") or {}) if play_res else {}
        sign_cookie = (play_res.get("signCookie") or play_data.get("signCookie") or "") if play_res else ""

        raw_streams = play_data.get("streamList") or play_data.get("streams") or []
        streams = []
        for s in raw_streams:
            u = s.get("url") or ""
            cookie_override = s.get("signCookie") or sign_cookie
            decoded_u = decode_sign_cookie_url(cookie_override) if (is_dummy_url(u) and cookie_override) else None
            real_url = decoded_u or u
            if not is_dummy_url(real_url):
                res_num = s.get("resolution") or s.get("resolutions") or 720
                try:
                    res_num = int(re.sub(r"\D", "", str(res_num)) or "720")
                except Exception:
                    res_num = 720
                streams.append(Stream(
                    id=str(s.get("id") or s.get("resourceId") or res_num),
                    format=s.get("format") or "MP4",
                    resolutions=f"{res_num}p",
                    codec=s.get("codecName") or "h264",
                    size_bytes=_number(s.get("size")),
                    url=real_url,
                    request_headers={"User-Agent": "Mozilla/5.0"}
                ))

        if not streams:
            ugc_first = make_official_request("GET", "/wefeed-mobile-bff/ugc/video/first-by-subject", {"subjectId": sid})
            ugc_data = (ugc_first.get("data") or {}) if ugc_first else {}
            belong_coll = ugc_data.get("belongToCollection") or {}
            coll_id = str(belong_coll.get("collectionId") or "")
            if coll_id:
                coll_res = make_official_request("GET", "/wefeed-mobile-bff/ugc/collection/download-list", {"collectionId": coll_id, "page": "1", "perPage": "50"})
                items = (coll_res.get("data") or {}).get("items") or [] if coll_res else []
                if items:
                    resources = items[0].get("resources") or []
                    for r in resources:
                        r_url = r.get("url") or ""
                        if not is_dummy_url(r_url):
                            res_num = int(re.sub(r"\D", "", str(r.get("resolutions") or 720)) or "720")
                            streams.append(Stream(
                                id=str(r.get("resourceId") or time.time()),
                                format="MP4",
                                resolutions=f"{res_num}p",
                                codec="h264",
                                size_bytes=_number(r.get("size")),
                                url=r_url,
                                request_headers={"User-Agent": "Mozilla/5.0"}
                            ))

        return Playback(title_id=sid, season=season, episode=episode, streams=streams)

    def captions(self, detail_path: str, stream_id: str, season: int = 0, episode: int = 0) -> list[Caption]:
        sid = detail_path
        if not sid.isdigit():
            try:
                detail = self.detail(detail_path)
                sid = detail.title.id
            except Exception:
                pass
        s_num = max(1, season)
        e_num = max(1, episode)
        res = make_official_request("GET", "/wefeed-mobile-bff/subject-api/get-ext-captions", {"subjectId": sid, "se": str(s_num), "ep": str(e_num)})
        sub_data = (res.get("data") or {}) if res else {}
        ext_list = sub_data.get("extCaptions") or sub_data.get("list") or []
        captions_list = []
        for sub in ext_list:
            url = sub.get("url") or ""
            if url:
                lan = sub.get("lan") or "en"
                captions_list.append(Caption(
                    language=lan,
                    label=sub.get("lanName") or lan,
                    format="srt",
                    url=url,
                    request_headers={"User-Agent": "Mozilla/5.0"}
                ))
        return captions_list
