import os
import re
import json
import base64
import time
import sys
import requests
from typing import Dict, Any, Tuple
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

CHANNELS_URL = "https://sportlink10-ajp.pages.dev/jtv.json"
COOKIE_URL = "https://allinonereborn2.online/jstrweb2/cookies.json"
SPORTS_COOKIE_URL = "https://allinonereborn2.online/jtv-fetch/jstarcookie/cookie.json"

M3U_FILE = "jtv.m3u"
JSON_FILE = "jtv.json"

USER_AGENT = "Sayan10"
MAX_RETRIES = 4
RETRY_DELAY = 5


# ---------------- RETRY FETCHER ----------------
def get_json(url: str) -> Any:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            fresh_url = f"{url}{'&' if '?' in url else '?'}t={int(time.time() * 1000)}"
            resp = requests.get(
                fresh_url,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache", "User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            if data is None or data == [] or data == {}:
                raise Exception("Empty response")
            print(f"[OK] Fetched {url} (attempt {attempt})")
            return data
        except Exception as e:
            last_error = e
            print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    raise Exception(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")


# ---------------- NORMAL COOKIE ----------------
def get_normal_cookie() -> str:
    try:
        data = get_json(COOKIE_URL)
    except Exception as e:
        print(f"[WARN] Cookie fetch failed: {e}")
        return ""

    if isinstance(data, str):
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("cookie"):
                return item["cookie"]
        return ""
    if isinstance(data, dict):
        return data.get("cookie") or ""
    return ""


# ---------------- COOKIE EXTRACTION ----------------
def _extract_cookie_from_url(url: str) -> Tuple[str, str]:
    """
    Extract embedded token/cookie params from a JioTV URL.

    JioTV sports URLs embed the Akamai token as a query param:
        https://.../WDVLive/index.mpd?__hdnea__=st=...~exp=...~hmac=...

    This token must be sent as an HTTP cookie header, NOT left in the URL
    query string, because many DASH/M3U players ignore query-string tokens.

    Also handles generic params: __cookie__, cookie, cookies.

    Returns (clean_url, cookie_string) where cookie_string includes the
    param name, e.g. "__hdnea__=st=...". If no cookie param exists,
    returns (url, "") with the URL untouched.
    """
    if not url:
        return url, ""

    parsed = urlparse(url)
    if not parsed.query:
        return url, ""

    params = parse_qs(parsed.query, keep_blank_values=True)
    cookie_val = ""

    # Priority: __hdnea__ (Akamai) first, then generic cookie params
    for key in ("__hdnea__", "__cookie__", "cookie", "cookies"):
        if key in params:
            raw = params.pop(key)[0]
            cookie_val = f"{key}={raw}"
            break

    if not cookie_val:
        return url, ""

    new_query = urlencode(params, doseq=True)
    clean_url = urlunparse((
        parsed.scheme, parsed.netloc, parsed.path,
        parsed.params, new_query, parsed.fragment,
    ))
    return clean_url, cookie_val


# ---------------- COOKIE EXPIRY ----------------
def get_cookie_expiry(cookie: str) -> str:
    """
    Parse `exp=<unix_ts>` from an __hdnea__ cookie and return it as
    a human-readable IST string, e.g. "20/9/2026 12:45:42 AM IST".
    Returns "" if no exp is present.
    """
    if not cookie:
        return ""
    m = re.search(r"exp=(\d+)", cookie)
    if not m:
        return ""
    try:
        exp = int(m.group(1))
        # UTC -> IST (UTC+05:30)
        dt = datetime.utcfromtimestamp(exp) + timedelta(hours=5, minutes=30)
        hour12 = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        return (
            f"{dt.day}/{dt.month}/{dt.year} "
            f"{hour12}:{dt.minute:02d}:{dt.second:02d} {ampm} IST"
        )
    except Exception:
        return ""


# ---------------- SPORTS DATA ----------------
def get_sports_data() -> Dict[str, Any]:
    """
    Returns:
        {
            "sportsIds": {channel_id, ...},
            "sportsCookies": {
                channel_id: {"url": clean_url, "cookie": cookie_string}
            }
        }
    """
    empty = {"sportsIds": set(), "sportsCookies": {}}

    try:
        data = get_json(SPORTS_COOKIE_URL)
    except Exception as e:
        print(f"[WARN] Sports fetch failed: {e}")
        return empty

    sports_cookies: Dict[str, Dict[str, str]] = {}
    results = (data.get("successful_results") or []) + (data.get("failed_results") or [])

    for item in results:
        if not isinstance(item, dict):
            continue
        channel_id = item.get("channel_id")
        if not channel_id:
            continue
        final_url = (
            item.get("final_url")
            or (item.get("error_details") or {}).get("final_url")
            or ""
        )
        if not final_url:
            continue

        final_url = re.sub(r"/output/", "/WDVLive/", final_url, count=1, flags=re.I)
        clean_url, embedded_cookie = _extract_cookie_from_url(final_url)

        sports_cookies[str(channel_id)] = {
            "url": clean_url,
            "cookie": embedded_cookie,
        }

    print(f"[INFO] Sports URLs loaded: {len(sports_cookies)}")
    return {"sportsIds": set(sports_cookies.keys()), "sportsCookies": sports_cookies}


# ---------------- HELPERS ----------------
def extract_keys(channel):
    key_id = channel.get("keyId") or ""
    key = channel.get("key") or ""
    if not key_id and isinstance(channel.get("clearkey"), dict) and channel.get("clearkey"):
        try:
            key_id, key = next(iter(channel["clearkey"].items()))
        except StopIteration:
            pass
    return key_id, key


def _clean_base_url(raw_url: str) -> str:
    """Strip the query string from a base channel URL."""
    parsed = urlparse(raw_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))


def resolve_channel(channel, normal_cookie, sports_cookies):
    """
    Single source of truth for URL + cookie resolution.

    Returns (final_url, cookie_value, is_sports).

    - Sports channel -> clean sports URL + extracted __hdnea__/cookie
    - Normal channel -> __hdnea__ extracted from URL if present,
                        else base URL + normal cookie
    """
    channel_id = str(channel.get("id") or "")

    if channel_id in sports_cookies:
        entry = sports_cookies[channel_id]
        if isinstance(entry, str):
            url, cookie = _extract_cookie_from_url(entry)
            return url, cookie, True
        return entry.get("url", ""), entry.get("cookie", ""), True

    # Normal channel: extract __hdnea__ if the URL carries one
    raw_url = channel.get("url") or ""
    clean_url, embedded_cookie = _extract_cookie_from_url(raw_url)
    if embedded_cookie:
        return clean_url, embedded_cookie, False
    return _clean_base_url(raw_url), normal_cookie, False


# ---------------- M3U ENTRY ----------------
def create_channel_entry(channel, normal_cookie="", sports_cookies=None):
    if sports_cookies is None:
        sports_cookies = {}

    channel_id = str(channel.get("id") or "")
    name       = channel.get("name") or ""
    logo       = channel.get("logo") or ""
    group      = channel.get("group") or channel.get("category") or "Other"
    raw_url    = channel.get("url") or ""

    key_id, key = extract_keys(channel)

    final_url, cookie_to_use, _is_sports = resolve_channel(
        channel, normal_cookie, sports_cookies
    )

    lines = []

    # EXTINF
    lines.append(
        f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" '
        f'tvg-logo="{logo}" group-title="{group}",{name}'
    )

    # DASH / MPD
    is_mpd = (
        channel.get("type") == "dash"
        or bool(re.search(r"\.mpd(?:\?|$)", final_url, re.I))
        or bool(re.search(r"\.mpd(?:\?|$)", raw_url, re.I))
    )

    if is_mpd:
        lines.append("#KODIPROP:inputstream=inputstream.adaptive")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")
        if key_id and key:
            lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            lines.append(f"#KODIPROP:inputstream.adaptive.license_key={key_id}:{key}")
        elif channel.get("license_url"):
            lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            lines.append(f'#KODIPROP:inputstream.adaptive.license_key={channel["license_url"]}')

    # Cookie — same treatment for sports AND normal channels
    if cookie_to_use:
        cookie_json = json.dumps({"cookie": cookie_to_use})
        lines.append(f"#EXTHTTP:{cookie_json}")

    lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
    lines.append(final_url)

    return "\n".join(lines)


# ---------------- JSON ENTRY ----------------
def build_channel_object(channel, normal_cookie="", sports_cookies=None):
    if sports_cookies is None:
        sports_cookies = {}

    key_id, key = extract_keys(channel)

    final_url, cookie_to_use, _is_sports = resolve_channel(
        channel, normal_cookie, sports_cookies
    )

    return {
        "id":             str(channel.get("id") or ""),
        "name":           channel.get("name") or "",
        "stream_url":     final_url,
        "cookie":         cookie_to_use,
        "cookie_expires": get_cookie_expiry(cookie_to_use),
        "key_id":         key_id,
        "key":            key,
        "logo":           channel.get("logo") or "",
    }


# ---------------- VALIDATE ----------------
def validate(channels, m3u_entries, json_entries):
    errors = []
    if not channels:
        errors.append("Channels list is empty")
    if not m3u_entries:
        errors.append("M3U output is empty")
    if not json_entries:
        errors.append("JSON output is empty")
    if len(m3u_entries) != len(json_entries):
        errors.append(f"Count mismatch: {len(m3u_entries)} M3U vs {len(json_entries)} JSON")
    if len(m3u_entries) < len(channels) * 0.8:
        errors.append(f"Too many channels dropped: expected ~{len(channels)}, got {len(m3u_entries)}")

    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] Validation passed: {len(m3u_entries)} channels")


# ---------------- GITHUB UPLOAD ----------------
def to_base64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def upload_to_github(filename: str, content: str):
    repo_owner = os.environ.get("GITHUB_OWNER") or os.environ.get("GITHUB_REPOSITORY", "").split("/")[0]
    repo_name  = os.environ.get("GITHUB_REPO")  or os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1]
    token      = os.environ.get("GITHUB_TOKEN")

    if not all([repo_owner, repo_name, token]):
        print(f"[WARN] GitHub credentials missing — skipping upload for {filename}")
        return

    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{filename}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Python-Script",
        "Accept": "application/vnd.github.v3+json",
    }

    existing = requests.get(api_url, headers=headers)
    sha = None
    if existing.status_code == 200:
        sha = existing.json().get("sha")
        existing_content = base64.b64decode(existing.json().get("content", "")).decode("utf-8")
        if existing_content.strip().replace("\r", "") == content.strip().replace("\r", ""):
            print(f"[INFO] No changes in {filename} — skipping commit")
            return

    payload = {
        "message": f"Auto-update {filename}: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "content": to_base64(content),
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload)
    if resp.ok:
        print(f"[OK] Uploaded {filename} to GitHub ({resp.status_code})")
    else:
        print(f"[ERROR] GitHub upload failed for {filename}: {resp.status_code} — {resp.text}", file=sys.stderr)


# ---------------- MAIN ----------------
def main():
    print(f"[START] {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")

    channels = get_json(CHANNELS_URL)
    if isinstance(channels, dict):
        channels = channels.get("channels") or channels.get("data") or []
    print(f"[INFO] Channels loaded: {len(channels)}")

    normal_cookie = get_normal_cookie()
    print(f"[INFO] Cookie: {'found' if normal_cookie else 'not found'}")

    sports_data = get_sports_data()

    m3u_entries  = []
    json_entries = []

    for ch in channels:
        try:
            m3u_entries.append(create_channel_entry(ch, normal_cookie, sports_data["sportsCookies"]))
            json_entries.append(build_channel_object(ch, normal_cookie, sports_data["sportsCookies"]))
        except Exception as e:
            print(f"[WARN] Skipped channel '{ch.get('name', '?')}': {e}", file=sys.stderr)

    validate(channels, m3u_entries, json_entries)

    timestamp   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    m3u_content = f'#EXTM3U x-tvg-url="" updated="{timestamp}"\n\n' + "\n\n".join(m3u_entries)
    json_content = json.dumps(json_entries, indent=2, ensure_ascii=False)

    # Save locally
    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"[INFO] M3U saved → {M3U_FILE}")

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        f.write(json_content)
    print(f"[INFO] JSON saved → {JSON_FILE}")

    # Optional GitHub API upload
    upload_to_github(M3U_FILE, m3u_content)
    upload_to_github(JSON_FILE, json_content)

    print("[DONE] All outputs written successfully.")


if __name__ == "__main__":
    main()
