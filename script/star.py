import json
import re
import requests
from datetime import datetime, timezone, timedelta

# ---------- configuration ----------
CHANNELS_URL = "https://sportlink-jtv.pages.dev/jtvp.json"
COOKIES_URL  = "https://allinonereborn2.online/jtv-fetch/jstarcookie/cookie.json"
OUTPUT_FILE  = "Star.m3u"

# Only keep channels whose name matches this pattern (case-insensitive)
NAME_FILTER = re.compile(r"star\s*sports", re.IGNORECASE)

USER_AGENT  = "Virat🐐"
GROUP_TITLE = "Sports"

IST = timezone(timedelta(hours=5, minutes=30))


# ---------- helper functions ----------
def format_expiry(exp_ts: str) -> str:
    """Convert a unix timestamp string to 'D/M/YYYY H:MM:SS AM/PM IST'."""
    try:
        dt = datetime.fromtimestamp(int(exp_ts), tz=IST)
    except (ValueError, OSError, TypeError):
        return ""
    hour12 = dt.hour % 12
    if hour12 == 0:
        hour12 = 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.day}/{dt.month}/{dt.year} {hour12}:{dt.minute:02d}:{dt.second:02d} {ampm} IST"


def get_cookie_expiry(cookie: str) -> str:
    """Extract exp=<unix_ts> from a __hdnea__ cookie string."""
    if not cookie:
        return ""
    exp_match = re.search(r"exp=(\d+)", cookie)
    return format_expiry(exp_match.group(1)) if exp_match else ""


def extract_hdnea(final_url: str):
    """Return the full __hdnea__ query string (including prefix) from a URL."""
    match = re.search(r"(__hdnea__=[^&]+)", final_url)
    return match.group(1) if match else None


def is_mpd(url: str) -> bool:
    """True if the URL path ends with .mpd, even if a query/hash is present."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).path.lower().endswith(".mpd")
    except Exception:
        return url.lower().split("?")[0].endswith(".mpd")


# ---------- fetch data ----------
channels_resp = requests.get(CHANNELS_URL)
channels_resp.raise_for_status()
channels = channels_resp.json()

cookies_resp = requests.get(COOKIES_URL)
cookies_resp.raise_for_status()
cookie_data = cookies_resp.json()

# Build a lookup: channel_id -> final_url
failed_map = {
    str(item["channel_id"]): item["error_details"]["final_url"]
    for item in cookie_data.get("failed_results", [])
}

# ---------- build M3U output (Star Sports only) ----------
lines = ["#EXTM3U", ""]
written = 0

for ch in channels:
    name = ch.get("name", "")
    if not NAME_FILTER.search(name):
        continue  # skip non–Star Sports channels

    cid = str(ch["id"])
    final_url = failed_map.get(cid)
    if not final_url:
        print(f"Warning: no cookie URL for channel {cid} ({name})")
        continue

    hdnea_full = extract_hdnea(final_url)
    if not hdnea_full:
        print(f"Warning: no __hdnea__ token found for channel {cid}")
        continue

    stream_url = ch["url"]
    logo       = ch.get("logo", "")
    key_id     = ch.get("keyId", "")
    key        = ch.get("key", "")
    group      = ch.get("group") or ch.get("category") or GROUP_TITLE

    # ---- EXTINF line ----
    lines.append(
        f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{name}" tvg-logo="{logo}" '
        f'group-title="{group}",{name}'
    )

    # ---- KODIPROP (adaptive + ClearKey) ----
    if is_mpd(stream_url):
        lines.append("#KODIPROP:inputstream=inputstream.adaptive")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")
        if key_id and key:
            lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            lines.append(
                f"#KODIPROP:inputstream.adaptive.license_key={key_id}:{key}"
            )

    # ---- __hdnea__ cookie sent as HTTP header (JSON form) ----
    lines.append(
        '#EXTHTTP:{"cookie": "%s"}' % hdnea_full
    )

    # ---- user-agent ----
    lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")

    # ---- stream URL (plain, no query params appended) ----
    lines.append(stream_url)
    lines.append("")  # blank line between entries

    written += 1

# ---------- write result ----------
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines).rstrip() + "\n")

print(f"✅ Star Sports M3U written to {OUTPUT_FILE} ({written} channels)")
