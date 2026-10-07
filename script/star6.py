#!/usr/bin/env python3
"""
Merge JioTV __hdnea__ cookies from M3U playlist into the JSON channel list.
Outputs the transformed schema with cookie_expires in IST.
"""

import json
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, unquote

M3U_URL  = "https://premiumplugx.top/jiostb/mjelo.php?view=raw"
JSON_URL = "https://sportlink-jtv.pages.dev/Star.json"
OUT_FILE = "star2.json"

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- helpers
def fetch(url: str) -> str:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def parse_m3u(m3u_text: str) -> dict:
    """
    Return {tvg_id: {"cookie": str, "key_id": str, "key": str}} from the M3U.
    Handles:
      - #EXTHTTP:{"cookie": "..."}
      - #KODIPROP:inputstream.adaptive.license_key=KEY_ID:KEY
      - __hdnea__=... inside the stream URL
    """
    entries = {}
    current_id = None
    current_data = {}

    for line in m3u_text.splitlines():
        line = line.strip()

        # Start of a new channel block
        if line.startswith("#EXTINF:"):
            # Save previous block if any
            if current_id and current_data:
                entries[current_id] = current_data
            # Extract tvg-id
            m = re.search(r'tvg-id="([^"]*)"', line)
            current_id = m.group(1) if m else None
            current_data = {"cookie": "", "key_id": "", "key": ""}

        # HTTP headers (rare in JioTV but kept for completeness)
        elif line.startswith("#EXTHTTP:") and current_id:
            payload = line[len("#EXTHTTP:"):].strip()
            try:
                data = json.loads(payload)
                cookie = data.get("cookie", "")
                if cookie:
                    current_data["cookie"] = cookie
            except json.JSONDecodeError:
                pass

        # DRM license key
        elif line.startswith("#KODIPROP:") and current_id:
            if "license_key=" in line:
                # Example: inputstream.adaptive.license_key=KEY_ID:KEY
                license_part = line.split("license_key=", 1)[1]
                if ":" in license_part:
                    kid, key = license_part.split(":", 1)
                    current_data["key_id"] = kid.strip()
                    current_data["key"] = key.strip()

        # Stream URL – extract __hdnea__ cookie
        elif line.startswith("http") and current_id:
            parsed = urlparse(line)
            qs = parse_qs(parsed.query)
            if "__hdnea__" in qs:
                # The value may be URL‑encoded; decode it
                cookie_val = unquote(qs["__hdnea__"][0])
                current_data["cookie"] = cookie_val

    # Save the last block
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

def transform(ch: dict, m3u_data: dict) -> dict:
    """Convert source JSON object to the target output schema."""
    # Prefer JSON fields; fall back to M3U for DRM keys
    key_id = ch.get("keyId") or m3u_data.get("key_id", "")
    key    = ch.get("key")   or m3u_data.get("key", "")
    cookie = m3u_data.get("cookie", "")

    return {
        "id":             str(ch.get("id", "")),
        "name":           ch.get("name", ""),
        "stream_url":     ch.get("url", ""),
        "cookie":         cookie,
        "cookie_expires": get_cookie_expiry(cookie),
        "key_id":         key_id,
        "key":            key,
        "logo":           ch.get("logo", ""),
    }

# ---------------------------------------------------------------- main
def merge_all(json_url: str, m3u_map: dict) -> list:
    """Merge cookies & DRM keys into every channel without filtering."""
    channels = json.loads(fetch(json_url))
    merged = 0
    result = []

    for ch in channels:
        cid = str(ch.get("id", ""))
        m3u_data = m3u_map.get(cid, {})
        if m3u_data.get("cookie"):
            merged += 1
        result.append(transform(ch, m3u_data))

    print(f"[+] Processed {len(result)} channels")
    print(f"[+] Cookies merged for {merged}/{len(result)} channels")
    return result

if __name__ == "__main__":
    print("[*] Fetching M3U...")
    m3u = fetch(M3U_URL)
    print(f"[+] {len(m3u):,} bytes")

    print("[*] Parsing M3U...")
    m3u_map = parse_m3u(m3u)
    print(f"[+] Found {len(m3u_map)} entries with cookie/DRM data")

    print("[*] Fetching JSON and merging...")
    result = merge_all(JSON_URL, m3u_map)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[+] Written -> {OUT_FILE}")
