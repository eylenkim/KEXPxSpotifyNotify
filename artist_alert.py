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

        # Look for the nearest preceding date heading
        date_str = ""
        for parent in h3.parents:
            prev = parent.find_previous(["h2", "h4"])
            if prev:
                date_str = prev.get_text(strip=True)
                break

        # Look for a time near the event (e.g. "11 a.m." or "3 p.m.")
        time_str = ""
        for tag in h3.find_all_previous(["h5", "p"], limit=5):
            text = tag.get_text(strip=True)
            if re.search(r"\d+\s*(a\.m\.|p\.m\.|am|pm)", text, re.IGNORECASE):
                time_str = text
                break

        details = f"{time_str} · KEXP Studio (Open to the Public)".strip(" ·")

        events.append({
            "artist": artist,
            "date": date_str,
            "details": details,
            "url": event_url,
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

def build_email_html(matches: list[dict]) -> str:
    rows = ""
    for m in matches:
        artist_cell = f'<a href="{m["url"]}" style="color:#1a1a1a; font-weight:600;">{m["artist"]}</a>' if m.get("url") else f'<strong>{m["artist"]}</strong>'
        rows += f"""
        <tr>
          <td style="padding:12px 16px;">{artist_cell}</td>
          <td style="padding:12px 16px; color:#555;">{m.get('date', '—')}</td>
          <td style="padding:12px 16px; color:#555;">{m.get('details', '—')}</td>
        </tr>
        """

    return f"""
    <html><body style="font-family: 'Helvetica Neue', Arial, sans-serif; background:#f5f5f5; margin:0; padding:24px;">
      <div style="max-width:620px; margin:0 auto; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <div style="background:#1c1c1c; padding:28px 32px;">
          <p style="color:#e8e8e8; margin:0; font-size:11px; text-transform:uppercase; letter-spacing:2px;">KEXP × Spotify</p>
          <h1 style="color:#fff; margin:8px 0 0; font-size:24px; font-weight:700;">
            {len(matches)} artist{'s' if len(matches) != 1 else ''} you know {'are' if len(matches) != 1 else 'is'} playing KEXP
          </h1>
        </div>
        <div style="padding:24px 32px;">
          <table style="width:100%; border-collapse:collapse;">
            <thead>
              <tr style="border-bottom:2px solid #eee;">
                <th style="padding:8px 16px; text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#999;">Artist</th>
                <th style="padding:8px 16px; text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#999;">Date</th>
                <th style="padding:8px 16px; text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#999;">Details</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div style="padding:16px 32px 28px; border-top:1px solid #eee;">
          <a href="https://www.kexp.org/events/" style="color:#1c1c1c; font-size:13px;">View all KEXP events →</a>
        </div>
        <div style="background:#f9f9f9; padding:14px 32px; font-size:11px; color:#aaa;">
          Sent weekly by your KEXP Artist Alert · {datetime.now().strftime('%B %d, %Y')}
        </div>
      </div>
    </body></html>
    """


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
    plain += "\nhttps://www.kexp.org/events/"

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
