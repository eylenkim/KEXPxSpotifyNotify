# KEXP Artist Alert 🎵

Weekly email alert when artists from your Spotify library are performing live at KEXP.

Runs automatically every Monday morning via GitHub Actions — no server required.

---

## How it works

1. Scrapes KEXP's events and live performance pages
2. Uses Claude to extract artist names from the page content
3. Compares against your followed artists, top artists, and saved tracks on Spotify
4. Emails you a clean digest of matches

---

## Setup (one time, ~15 minutes)

### Step 1 — Fork or create a private GitHub repo

Push all these files to a **private** GitHub repo (private keeps your secrets safe).

### Step 2 — Create a Spotify app

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Click **Create App**
3. Name it anything (e.g. "KEXP Alert")
4. Set Redirect URI to: `http://localhost:8888/callback`
5. Copy your **Client ID** and **Client Secret**

### Step 3 — Get your Spotify refresh token

Run this locally once:

```bash
pip install spotipy
python spotify_auth.py
```

A browser will open → log in to Spotify → paste the URL it redirects you to back in the terminal.
Copy the **refresh token** it prints out.

### Step 4 — Enable Gmail App Password

1. Go to your Google Account → Security → 2-Step Verification (must be enabled)
2. Search for **App Passwords**
3. Generate a new app password for "Mail"
4. Copy the 16-character password

### Step 5 — Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add all of these:

| Secret name             | Value                                      |
|-------------------------|--------------------------------------------|
| `SPOTIFY_CLIENT_ID`     | From your Spotify app dashboard            |
| `SPOTIFY_CLIENT_SECRET` | From your Spotify app dashboard            |
| `SPOTIFY_REFRESH_TOKEN` | From step 3 above                          |
| `GMAIL_ADDRESS`         | Your Gmail address (e.g. you@gmail.com)    |
| `GMAIL_APP_PASSWORD`    | The 16-char app password from step 4       |
| `ALERT_EMAIL`           | Where to send alerts (can be same as Gmail)|
| `ANTHROPIC_API_KEY`     | From [console.anthropic.com](https://console.anthropic.com) |

### Step 6 — Test it

Go to **Actions → KEXP Artist Alert → Run workflow** to trigger it manually and confirm it works before waiting for Monday.

---

## Customization

**Change the schedule** — edit the `cron` line in `.github/workflows/weekly_alert.yml`:
- `"0 16 * * 1"` = every Monday 9am PDT
- `"0 16 * * 0,3"` = twice a week (Sunday + Wednesday)

**Add more KEXP URLs** — edit the `KEXP_URLS` list in `artist_alert.py` if KEXP adds new event pages.

**Adjust Spotify sources** — the script pulls from followed artists, top artists (all time ranges), and saved tracks. You can remove sections you don't want in the `get_spotify_artists()` function.

---

## Cost

- GitHub Actions: free (well within the free tier for a weekly job)
- Anthropic API: a few cents per run (the Claude call processes ~3k tokens)
- Spotify API: free
