#!/usr/bin/env python3
"""Generate a new Dropbox refresh token.

Usage:
    python3 scripts/generate_dropbox_token.py

This script will:
1. Prompt you for app key and app secret
2. Guide you through the OAuth flow
3. Output the refresh token to use in .env
"""

import sys
import webbrowser
from urllib.parse import urlencode

import requests


def main():
    print("=" * 70)
    print("Dropbox Refresh Token Generator")
    print("=" * 70)
    print()

    # Get app credentials
    app_key = input("Enter your Dropbox App Key: ").strip()
    app_secret = input("Enter your Dropbox App Secret: ").strip()

    if not app_key or not app_secret:
        print("❌ Error: App Key and App Secret are required")
        sys.exit(1)

    # Step 1: Get authorization code
    print()
    print("Step 1: Getting authorization code...")
    print()

    auth_url = "https://www.dropbox.com/oauth2/authorize"
    params = {
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",  # Get refresh token
        "redirect_uri": "http://localhost:8080",
    }

    full_auth_url = f"{auth_url}?{urlencode(params)}"
    print(f"Opening browser to: {full_auth_url}")
    print()

    try:
        webbrowser.open(full_auth_url)
    except Exception as e:
        print(f"Could not open browser: {e}")
        print(f"Manually visit: {full_auth_url}")

    print()
    auth_code = input("Enter the authorization code from the redirect URL: ").strip()

    if not auth_code:
        print("❌ Error: Authorization code is required")
        sys.exit(1)

    # Step 2: Exchange code for refresh token
    print()
    print("Step 2: Exchanging code for refresh token...")
    print()

    token_url = "https://api.dropboxapi.com/oauth2/token"
    data = {
        "code": auth_code,
        "grant_type": "authorization_code",
        "client_id": app_key,
        "client_secret": app_secret,
    }

    try:
        response = requests.post(token_url, data=data, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error exchanging code: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response: {e.response.text}")
        sys.exit(1)

    token_data = response.json()

    if "error" in token_data:
        print(f"❌ Error: {token_data['error']}")
        if "error_description" in token_data:
            print(f"   {token_data['error_description']}")
        sys.exit(1)

    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", "unknown")

    if not refresh_token:
        print("❌ Error: No refresh token in response")
        print(f"Response: {token_data}")
        sys.exit(1)

    # Success!
    print()
    print("=" * 70)
    print("✅ SUCCESS! Here's your new refresh token:")
    print("=" * 70)
    print()
    print(f"Token: {refresh_token}")
    print()
    print("Save this in your .env file:")
    print(f"MAKER8_DROPBOX_REFRESH_TOKEN={refresh_token}")
    print()
    print("Or set it as environment variable:")
    print(f"export MAKER8_DROPBOX_REFRESH_TOKEN='{refresh_token}'")
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
