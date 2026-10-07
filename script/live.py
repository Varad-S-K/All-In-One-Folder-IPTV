import json
import re
import urllib.request
import datetime

# Configuration
JSON_URL = "https://raw.githubusercontent.com/darkbyteprojects/iptv_png/refs/heads/main/provider_2/live_events.json"
OUTPUT_FILE = "LiveEvent.m3u"


def fetch_json(url):
    """Fetch JSON data from the given URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def build_m3u_header():
    """Build the M3U header with credits and last update timestamp."""
    now = datetime.datetime.now()
    timestamp = now.strftime("%I:%M %p %m-%d-%Y")

    header_lines = [
        "#EXTM3U",
        "#PLAYLIST:Live Events",
        f"#LAST_UPDATE:{timestamp}",
        "#Created by - Sayan 10",
    ]
    return "\n".join(header_lines) + "\n"


def parse_url_params(raw_url):
    """
    Split raw URL into clean stream URL and header/query params.
    Handles:
      url|User-Agent=...&Referer=...
      url?md5=...&expires=...|origin=...
      url?|user-agent=...
      url?User-Agent=...&Referer=...
    """
    if "|" in raw_url:
        stream_url, param_string = raw_url.split("|", 1)
    else:
        stream_url, param_string = raw_url, ""

    # If no pipe, but query string looks like header params, split it.
    if not param_string and "?" in stream_url:
        base, query = stream_url.split("?", 1)
        if re.search(r"(?i)(user-agent|referer|origin)=", query):
            stream_url = base
            param_string = query

    stream_url = stream_url.rstrip("?&")

    params = {}
    if param_string:
        param_string = param_string.lstrip("?")
        for pair in param_string.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key.strip()] = value.strip()

    return stream_url, params


def get_event_name(item):
    """Build a readable event name from Provider 2 schema."""
    info = item.get("eventInfo", {})
    event_name = info.get("eventName") or item.get("title") or "Unknown"

    team_a = info.get("teamA")
    team_b = info.get("teamB")

    if team_a and team_b and team_a != team_b:
        return f"{event_name}: {team_a} vs {team_b}"

    return event_name


def generate_m3u_entry(item, stream):
    """Generate a single M3U entry from one resolved stream."""
    info = item.get("eventInfo", {})

    event_name = get_event_name(item)
    stream_title = (stream.get("title") or "").strip()
    name = f"{event_name} - {stream_title}" if stream_title else event_name

    tvg_id = str(item.get("id", item.get("slug", "")))
    category = (info.get("eventCat") or item.get("cat") or "Live Events").strip()

    logo = info.get("eventLogo") or item.get("image") or info.get("teamAFlag") or ""
    if logo == "null":
        logo = ""

    raw_url = (stream.get("link") or "").strip()

    # Skip invalid / empty links
    if not raw_url or not raw_url.startswith(("http://", "https://")):
        return None

    stream_url, params = parse_url_params(raw_url)

    # DRM / stream type
    api = (stream.get("api") or "").strip()
    stream_type = str(stream.get("type", "0"))

    is_dash = ".mpd" in stream_url.lower() or stream_type == "1"
    is_hls = ".m3u8" in stream_url.lower()

    lines = []

    # EXTINF line
    extinf = (
        f'#EXTINF:-1 tvg-id="{tvg_id}" '
        f'tvg-name="{name}" '
        f'tvg-logo="{logo}" '
        f'group-title="{category}",{name}'
    )
    lines.append(extinf)

    # DASH / HLS properties
    if is_dash:
        lines.append("#KODIPROP:inputstream=inputstream.adaptive")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")

        if api and ":" in api:
            key_id, key = api.split(":", 1)
            lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            lines.append(
                f"#KODIPROP:inputstream.adaptive.license_key={key_id}:{key}"
            )

    elif is_hls:
        lines.append("#KODIPROP:inputstream=inputstream.ffmpeg")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=hls")

    # Headers
    user_agent = (
        params.get("user-agent")
        or params.get("User-Agent")
        or params.get("user_agent")
    )
    if user_agent:
        lines.append(f"#EXTVLCOPT:http-user-agent={user_agent}")

    referer = params.get("Referer") or params.get("referer")
    if referer:
        lines.append(f"#EXTVLCOPT:http-referrer={referer}")

    origin = params.get("Origin") or params.get("origin")
    if origin:
        lines.append(f"#EXTVLCOPT:http-origin={origin}")

    # Final URL
    lines.append(stream_url)

    return "\n".join(lines)


def main():
    try:
        data = fetch_json(JSON_URL)
    except Exception as e:
        print(f"Error fetching JSON: {e}")
        return

    m3u_content = build_m3u_header()
    entry_count = 0

    for item in data:
        for stream in item.get("resolved_streams", []):
            entry = generate_m3u_entry(item, stream)
            if entry:
                m3u_content += entry + "\n\n"
                entry_count += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"Successfully generated {OUTPUT_FILE} with {entry_count} entries.")


if __name__ == "__main__":
    main()
