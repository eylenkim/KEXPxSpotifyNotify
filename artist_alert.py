"""
KEXP Artist Alert
-----------------
Scrapes KEXP's upcoming events, compares against your Spotify library,
and emails you when a match is found. Designed to run weekly via GitHub Actions.
"""

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import spotipy
from spotipy.oauth2 import SpotifyOAuth


# ── Config from environment variables (set as GitHub Secrets) ──────────────
SPOTIFY_CLIENT_ID     = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"]

GMAIL_ADDRESS         = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD    = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAIL           = os.environ.get("ALERT_EMAIL", GMAIL_ADDRESS)

# KEXP public events page (pre-filtered to public/open events only)
KEXP_URL = "https://www.kexp.org/events/kexp-events/?category=public"

# Matches titles like "Ratboys LIVE on KEXP (OPEN TO THE PUBLIC)"
TITLE_RE = re.compile(r"^(.+?)\s+LIVE on KEXP", re.IGNORECASE)


# ── 1. Scrape KEXP ─────────────────────────────────────────────────────────

def parse_date_and_time(h3):
    """
    Parse date + time from the Add to Calendar text near an event h3.
    Calendar links contain raw strings like: 04/29/2026 11:00
    Returns (formatted_date, formatted_time) e.g. ("Wednesday, Apr 29, 2026", "11 a.m.")
    """
    for tag in h3.find_all_next(string=re.compile(r"\d{2}/\d{2}/\d{4}")):
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", tag)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%m/%d/%Y")
                hour, minute = int(m.group(2)[:2]), int(m.group(2)[3:])
                period = "a.m." if hour < 12 else "p.m."
                hour12 = hour % 12 or 12
                time_fmt = f"{hour12} {period}" if minute == 0 else f"{hour12}:{minute:02d} {period}"
                date_fmt = dt.strftime("%A, %b %-d, %Y")
                return date_fmt, time_fmt
            except ValueError:
                pass
    return "", ""


def get_kexp_events() -> list[dict]:
    """
    Scrapes the KEXP public events page directly.
    All public in-studio events follow the pattern:
      "[Artist] LIVE on KEXP (OPEN TO THE PUBLIC)"
    so we parse them reliably without needing an LLM call.
    """
    print(f"  Fetching {KEXP_URL} …")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; KexpArtistAlert/1.0)"}
    resp = requests.get(KEXP_URL, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    for h3 in soup.find_all("h3"):
        link = h3.find("a")
        if not link:
            continue
        title = link.get_text(strip=True)

        # Only process titles that say "OPEN TO THE PUBLIC"
        if "OPEN TO THE PUBLIC" not in title.upper():
            continue

        match = TITLE_RE.match(title)
        if not match:
            continue

        artist = match.group(1).strip()
        event_url = link.get("href", "")
        if event_url and not event_url.startswith("http"):
            event_url = "https://www.kexp.org" + event_url

        # Parse date + time from nearby calendar text
        date_str, time_str = parse_date_and_time(h3)

        # Grab the artist photo from the nearest <img> before the h3
        photo_url = ""
        for img in h3.find_all_previous("img", limit=5):
            src = img.get("src", "")
            if not src or "placeholder" in src or "logo" in src or "icon" in src or src.endswith(".svg"):
                continue
            if src.startswith("/"):
                src = "https://www.kexp.org" + src
            photo_url = src
            break

        details = f"{time_str} · KEXP Studio (Open to the Public)".strip(" ·")

        events.append({
            "artist": artist,
            "date": date_str,
            "details": details,
            "url": event_url,
            "photo": photo_url,
        })

    print(f"    → Found {len(events)} public events")
    return events


# ── 2. Get Spotify artists ─────────────────────────────────────────────────

def get_spotify_artists() -> set[str]:
    """Returns a set of lowercase artist names from your Spotify library."""
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="user-follow-read user-top-read user-library-read",
    ))

    # Re-auth using stored refresh token (no browser needed in CI)
    sp.auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)

    artist_names = set()

    # Followed artists
    print("  Fetching followed artists …")
    results = sp.current_user_followed_artists(limit=50)
    while results:
        for a in results["artists"]["items"]:
            artist_names.add(a["name"].lower().strip())
        results = sp.next(results["artists"]) if results["artists"]["next"] else None

    # Top artists (short, medium, long term)
    print("  Fetching top artists …")
    for term in ["short_term", "medium_term", "long_term"]:
        tops = sp.current_user_top_artists(limit=50, time_range=term)
        for a in tops["items"]:
            artist_names.add(a["name"].lower().strip())

    # Artists from ALL saved tracks — full library scan
    print("  Scanning full liked songs library …")
    offset = 0
    while True:
        tracks = sp.current_user_saved_tracks(limit=50, offset=offset)
        if not tracks["items"]:
            break
        for item in tracks["items"]:
            for a in item["track"]["artists"]:
                artist_names.add(a["name"].lower().strip())
        offset += 50
        if not tracks["next"]:
            break
        print(f"    … {offset} tracks scanned so far")

    print(f"  → {len(artist_names)} unique Spotify artists loaded")
    return artist_names


# ── 3. Match ───────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    """Fuzzy-normalize for matching: lowercase, strip 'the', drop punctuation."""
    name = name.lower().strip()
    name = re.sub(r"^the\s+", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def find_matches(kexp_events: list[dict], spotify_artists: set[str]) -> list[dict]:
    spotify_normalized = {normalize(a): a for a in spotify_artists}
    matches = []

    for event in kexp_events:
        kexp_norm = normalize(event["artist"])
        if kexp_norm in spotify_normalized:
            matches.append({
                **event,
                "spotify_name": spotify_normalized[kexp_norm],
            })

    # Deduplicate by artist
    seen = set()
    unique = []
    for m in matches:
        if m["artist"].lower() not in seen:
            seen.add(m["artist"].lower())
            unique.append(m)

    return unique


# ── 4. Send email ──────────────────────────────────────────────────────────

def build_artist_card(m: dict) -> str:
    if m.get("photo"):
        photo_html = f'<img src="{m["photo"]}" width="96" height="96" style="width:96px;height:96px;border-radius:50%;object-fit:cover;display:block;margin:0 auto 18px;" alt="">'
    else:
        photo_html = '<div style="width:96px;height:96px;border-radius:50%;background:#e8e8e8;margin:0 auto 18px;"></div>'

    artist_link = f'<a href="{m["url"]}" style="color:#1a1a1a;text-decoration:none;">{m["artist"]}</a>' if m.get("url") else m["artist"]

    event_btn = ""
    if m.get("url"):
        event_btn = f'<a href="{m["url"]}" style="display:block;width:100%;padding:14px 20px;background:#1a1a1a;color:#fff;text-decoration:none;border-radius:8px;font-size:15px;font-weight:500;text-align:center;box-sizing:border-box;margin-bottom:10px;">View KEXP event</a>'

    all_btn = '<a href="https://www.kexp.org/events/kexp-events/?category=public" style="display:block;width:100%;padding:14px 20px;background:#fff;color:#1a1a1a;text-decoration:none;border-radius:8px;font-size:15px;font-weight:500;text-align:center;box-sizing:border-box;border:1px solid #ddd;">View all KEXP events</a>'

    return f"""
    <div style="padding:32px 24px;border-bottom:1px solid #f0f0f0;text-align:center;">
      {photo_html}
      <p style="margin:0 0 8px;font-size:22px;font-weight:600;color:#1a1a1a;">{artist_link}</p>
      <p style="margin:0 0 6px;font-size:16px;font-weight:500;color:#444;">{m.get("date","")}</p>
      <p style="margin:0 0 24px;font-size:15px;color:#888;">{m.get("details","")}</p>
      {event_btn}{all_btn}
    </div>
    """


def build_email_html(matches: list[dict]) -> str:
    cards = "".join(build_artist_card(m) for m in matches)
    count = len(matches)
    noun = f"{count} artist{'s' if count != 1 else ''}"
    verb = "are" if count != 1 else "is"

    return f"""<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:16px;background:#f2f2f2;font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;}}
  .wrap{{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;}}
  .last-card{{border-bottom:none!important;}}
</style>
</head><body>
<div class="wrap">
  <div style="background:#1a1a1a;padding:28px 24px 24px;">
    <p style="color:#888;margin:0 0 8px;font-size:11px;text-transform:uppercase;letter-spacing:2px;">KEXP x Spotify</p>
    <h1 style="color:#fff;margin:0;font-size:22px;font-weight:600;line-height:1.3;">{noun} you know {verb} playing KEXP</h1>
  </div>
  {cards}
  <div style="padding:16px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:12px;color:#aaa;">KEXP Artist Alert &middot; {datetime.now().strftime('%B %d, %Y')}</p>
  </div>
</div>
</body></html>"""


def send_email(matches: list[dict]):
    subject = f"🎵 {len(matches)} of your artists at KEXP this week"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ALERT_EMAIL

    # Plain text fallback
    plain = f"KEXP Artist Alert — {datetime.now().strftime('%B %d, %Y')}\n\n"
    for m in matches:
        plain += f"• {m['artist']}  |  {m.get('date','?')}  |  {m.get('details','')}\n"
        if m.get("url"):
            plain += f"  {m['url']}\n"
    plain += f"\nhttps://www.kexp.org/events/kexp-events/?category=public"

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(build_email_html(matches), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, ALERT_EMAIL, msg.as_string())

    print(f"  ✉️  Email sent to {ALERT_EMAIL}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("\n🎵 KEXP Artist Alert\n")

    print("📅 Scraping KEXP events …")
    kexp_events = get_kexp_events()

    if not kexp_events:
        print("  No events found — KEXP page structure may have changed.")
        return

    print(f"\n🎧 Loading Spotify library …")
    spotify_artists = get_spotify_artists()

    print(f"\n🔍 Matching …")
    matches = find_matches(kexp_events, spotify_artists)
    print(f"  → {len(matches)} match{'es' if len(matches) != 1 else ''} found")

    if not matches:
        print("  No matches this week. No email sent.")
        return

    for m in matches:
        print(f"  ✅  {m['artist']}  ({m.get('date', 'date TBD')})")

    print(f"\n📧 Sending alert email …")
    send_email(matches)

    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
