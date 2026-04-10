"""
spotify_auth.py
---------------
Run this ONCE locally to generate your Spotify refresh token.
Paste the token into GitHub Secrets as SPOTIFY_REFRESH_TOKEN.

Usage:
    pip install spotipy
    python spotify_auth.py
"""

import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Paste your Spotify app credentials here (from developer.spotify.com)
CLIENT_ID     = input("Spotify Client ID: ").strip()
CLIENT_SECRET = input("Spotify Client Secret: ").strip()

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-follow-read user-top-read user-library-read",
))

# This will open a browser for you to log in — after login it redirects to localhost
# Spotipy captures the code automatically
token_info = sp.auth_manager.get_cached_token()

if not token_info:
    # Trigger the full auth flow
    sp.current_user()  # forces login
    token_info = sp.auth_manager.get_cached_token()

print("\n✅ Authentication successful!")
print(f"\nYour refresh token (add to GitHub Secrets as SPOTIFY_REFRESH_TOKEN):\n")
print(token_info["refresh_token"])
print()
