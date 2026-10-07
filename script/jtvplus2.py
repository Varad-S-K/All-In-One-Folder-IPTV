#!/usr/bin/env python3
"""
Fetch JioTV M3U playlist (with OTT Navigator User-Agent) and parse
cookies from #EXTHTTP and DRM keys from #KODIPROP.
"""

import urllib.request
import urllib.error
import json
import re
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timezone, timedelta

M3U_URL = "https://premiumplugx.top/jiostb/mjelo.php?view=raw"
OUT_FILE = "jtvplus2.m3u"
IST = timezone(timedelta(hours=5, minutes=30))

def fetch_playlist(url: str, user_agent: str = "OTT Navigator") -> str:
    """Fetch M3U playlist with a specific User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise

def parse_m3u(m3u_text: str) -> dict:
    """
    Parse M3U text into a dict keyed by tvg-id:
    {tvg_id: {"cookie": str, "key_id": str, "key": str, "user_agent": str}}
    """
    entries = {}
    current_id = None
    current_data = {}

    for line in m3u_text.splitlines():
        line = line.strip()

        if line.startswith("#EXTINF:"):
            if current_id and current_data:
                entries[current_id] = current_data
            m = re.search(r'tvg-id="([^"]*)"', line)
            current_id = m.group(1) if m else None
            current_data = {"cookie": "", "key_id": "", "key": "", "user_agent": ""}

        elif line.startswith("#EXTHTTP:") and current_id:
            payload = line[len("#EXTHTTP:"):].strip()
            try:
                data = json.loads(payload)
                cookie = data.get("cookie", "")
                if cookie:
                    current_data["cookie"] = cookie
            except json.JSONDecodeError:
                pass

        elif line.startswith("#KODIPROP:") and current_id:
            if "license_key=" in line:
                license_part = line.split("license_key=", 1)[1]
                if ":" in license_part:
                    kid, key = license_part.split(":", 1)
                    current_data["key_id"] = kid.strip()
                    current_data["key"] = key.strip()

        elif line.startswith("#EXTVLCOPT:") and current_id:
            if "http-user-agent=" in line:
                ua = line.split("http-user-agent=", 1)[1]
                current_data["user_agent"] = ua.strip()

        elif line.startswith("http") and current_id:
            # Fallback: extract __hdnea__ from URL if not already set via #EXTHTTP
            if not current_data.get("cookie"):
                parsed = urlparse(line)
                qs = parse_qs(parsed.query)
                if "__hdnea__" in qs:
                    current_data["cookie"] = unquote(qs["__hdnea__"][0])

    if current_id and current_data:
        entries[current_id] = current_data

    return entries

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
    """Extract exp=<unix_ts> from a __hdnea__ cookie and format it in IST."""
    if not cookie:
        return ""
    exp_match = re.search(r"exp=(\d+)", cookie)
    if not exp_match:
        return ""
    return format_expiry(exp_match.group(1))

# ---------------------------------------------------------------- main
if __name__ == "__main__":
    print(f"[*] Fetching playlist from {M3U_URL}...")
    m3u = fetch_playlist(M3U_URL)
    print(f"[+] {len(m3u):,} bytes downloaded")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u)
    print(f"[+] Saved -> {OUT_FILE}")

    print("[*] Parsing M3U...")
    m3u_map = parse_m3u(m3u)
    print(f"[+] Parsed {len(m3u_map)} channel entries")

    # Example: print first entry
    for cid, data in list(m3u_map.items())[:1]:
        print(f"\n[*] Sample entry (tvg-id={cid}):")
        print(f"    Cookie: {data['cookie'][:80]}...")
        print(f"    Expiry: {get_cookie_expiry(data['cookie'])}")
        print(f"    Key ID: {data['key_id']}")
        print(f"    Key:    {data['key']}")
        print(f"    UA:     {data['user_agent']}")
