"""
WarcraftLogs OAuth2 User Token Authentication Script.

This script opens the system browser for the user to authenticate with WarcraftLogs,
then captures the authorization code via local HTTP callback and exchanges it for an access token.

Usage:
    python wcl_authenticate.py
    python wcl_authenticate.py --manual   # Get URL to open in your own browser

The script will:
1. Start a local HTTP server to receive the OAuth callback
2. Open your default browser to the WarcraftLogs authorization page
3. Wait for you to log in and authorize the application
4. Capture the authorization code from the redirect
5. Exchange the code for an access token
6. Save the token to wcl_user_token.json

The token is automatically saved to your config file.
"""

import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional
import json
import sys
import threading
import socket
import requests

# Add src to path for imports
src_path = Path(__file__).resolve().parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from wowlc.core.paths import get_path_manager
from wowlc.core.config import get_config_manager

# Configuration
WCL_AUTH_URL = "https://www.warcraftlogs.com/oauth/authorize"
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

# Get PathManager and ConfigManager instances
paths = get_path_manager()
config = get_config_manager()

# Redirect URI - must match your WarcraftLogs app settings
REDIRECT_URI = config.get_wcl_redirect_uri()

# Where to save the token
TOKEN_STORAGE_PATH = paths.get_wcl_token_path()


@dataclass
class StoredToken:
    """Stored OAuth token data."""
    access_token: str
    token_type: str
    expires_in: int
    created_at: str
    expires_at: Optional[str] = None
    refresh_token: Optional[str] = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback."""

    # Class-level storage for the authorization result
    auth_code: Optional[str] = None
    auth_error: Optional[str] = None

    def log_message(self, format, *args):
        """Suppress HTTP server logging."""
        pass

    def do_GET(self):
        """Handle GET request (OAuth callback)."""
        # Parse the callback URL
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)

        if 'code' in query_params:
            OAuthCallbackHandler.auth_code = query_params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            success_html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authorization Successful</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                           display: flex; justify-content: center; align-items: center; height: 100vh;
                           margin: 0; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; }
                    .container { text-align: center; padding: 40px; }
                    h1 { color: #4ade80; margin-bottom: 20px; }
                    p { color: #94a3b8; font-size: 18px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>✓ Authorization Successful!</h1>
                    <p>You can close this window and return to the application.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode('utf-8'))
        elif 'error' in query_params:
            error = query_params.get('error', ['Unknown'])[0]
            error_desc = query_params.get('error_description', ['No description'])[0]
            OAuthCallbackHandler.auth_error = f"{error}: {error_desc}"
            self.send_response(400)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authorization Failed</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                           display: flex; justify-content: center; align-items: center; height: 100vh;
                           margin: 0; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; }}
                    .container {{ text-align: center; padding: 40px; }}
                    h1 {{ color: #ef4444; margin-bottom: 20px; }}
                    p {{ color: #94a3b8; font-size: 18px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>✗ Authorization Failed</h1>
                    <p>{error}: {error_desc}</p>
                    <p>Please close this window and try again.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(error_html.encode('utf-8'))
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Invalid callback - no authorization code found</h1></body></html>')


def save_token(token_data: dict) -> None:
    """Save the token data to a JSON file."""
    TOKEN_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Calculate expiry time
    expires_in = token_data.get("expires_in", 3600)
    created_at = datetime.now()
    expires_at = None
    if expires_in:
        expires_at = (created_at + timedelta(seconds=expires_in)).isoformat()

    stored = StoredToken(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type", "Bearer"),
        expires_in=expires_in,
        created_at=created_at.isoformat(),
        expires_at=expires_at,
        refresh_token=token_data.get("refresh_token"),
    )

    with open(TOKEN_STORAGE_PATH, "w") as f:
        json.dump(asdict(stored), f, indent=2)

    print(f"✅ Token saved to {TOKEN_STORAGE_PATH}")


def exchange_code_for_token(code: str, client_id: str, client_secret: str) -> dict:
    """Exchange the authorization code for an access token."""
    print("\n🔄 Exchanging authorization code for access token...")

    response = requests.post(
        WCL_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )

    if response.status_code != 200:
        print(f"❌ Token exchange failed: {response.status_code}")
        print(f"   Response: {response.text}")
        raise Exception(f"Token exchange failed: {response.text}")

    token_data = response.json()

    if "access_token" not in token_data:
        print(f"❌ No access token in response: {token_data}")
        raise Exception("No access token in response")

    print("✅ Successfully obtained access token!")
    return token_data


def get_auth_url(client_id: str) -> str:
    """Build the authorization URL."""
    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    }
    return f"{WCL_AUTH_URL}?{urlencode(auth_params)}"


def get_callback_port() -> int:
    """Extract port from redirect URI."""
    parsed = urlparse(REDIRECT_URI)
    return parsed.port or 80


def manual_auth_flow(client_id: str, client_secret: str) -> dict:
    """
    Guide the user through manual authentication in their own browser.
    """
    auth_url = get_auth_url(client_id)

    print("\n" + "=" * 60)
    print("MANUAL AUTHENTICATION")
    print("=" * 60)
    print("\n1. Open this URL in your browser:\n")
    print(f"   {auth_url}")
    print("\n2. Log in and authorize the application")
    print("\n3. You'll be redirected to a page that won't load.")
    print("   Copy the ENTIRE URL from your browser's address bar.")
    print("   It will look like: http://localhost:8765/callback?code=abc123...")

    callback_url = input("\n4. Paste the full callback URL here: ").strip()

    if not callback_url:
        raise Exception("No URL provided")

    # Parse the code from the URL
    parsed = urlparse(callback_url)
    query_params = parse_qs(parsed.query)

    if "code" not in query_params:
        if "error" in query_params:
            error = query_params.get("error", ["Unknown"])[0]
            error_desc = query_params.get("error_description", ["No description"])[0]
            raise Exception(f"Authorization failed: {error} - {error_desc}")
        raise Exception(f"No authorization code found in URL: {callback_url}")

    code = query_params["code"][0]
    print(f"\n🔑 Got authorization code: {code[:20]}...")

    # Exchange code for token
    token_data = exchange_code_for_token(code, client_id, client_secret)

    # Save the token
    save_token(token_data)

    return token_data


def authenticate(client_id: str, client_secret: str) -> dict:
    """
    Open system browser for WarcraftLogs OAuth authentication.

    Uses a local HTTP server to capture the OAuth callback.

    Args:
        client_id: WarcraftLogs OAuth client ID
        client_secret: WarcraftLogs OAuth client secret

    Returns:
        Token data dictionary with access_token, etc.
    """
    # Reset class-level state
    OAuthCallbackHandler.auth_code = None
    OAuthCallbackHandler.auth_error = None

    # Get the port from redirect URI
    port = get_callback_port()

    # Check if port is available
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('localhost', port))
        sock.close()
    except OSError:
        raise Exception(f"Port {port} is already in use. Close the application using it and try again.")

    # Create HTTP server
    server = HTTPServer(('localhost', port), OAuthCallbackHandler)
    server.timeout = 300  # 5 minute timeout

    # Build auth URL
    auth_url = get_auth_url(client_id)

    print("🌐 Opening browser for WarcraftLogs authentication...")
    print("   Please log in and authorize the application.")
    print(f"   Waiting for callback on http://localhost:{port}...\n")

    # Open browser
    if not webbrowser.open(auth_url):
        print("⚠ Could not open browser automatically.")
        print(f"   Please open this URL manually:\n   {auth_url}")

    # Wait for callback (single request)
    try:
        server.handle_request()
    except Exception as e:
        raise Exception(f"Error waiting for callback: {e}")
    finally:
        server.server_close()

    # Check result
    if OAuthCallbackHandler.auth_error:
        raise Exception(f"Authorization failed: {OAuthCallbackHandler.auth_error}")

    if not OAuthCallbackHandler.auth_code:
        raise Exception("No authorization code received. Authentication timed out or was cancelled.")

    code = OAuthCallbackHandler.auth_code
    print(f"🔑 Got authorization code: {code[:20]}...")

    # Exchange code for token
    token_data = exchange_code_for_token(code, client_id, client_secret)

    # Save the token
    save_token(token_data)

    return token_data


def load_existing_token() -> Optional[dict]:
    """Load existing token from storage if it exists and is valid."""
    if not TOKEN_STORAGE_PATH.exists():
        return None

    try:
        with open(TOKEN_STORAGE_PATH) as f:
            data = json.load(f)

        # Check if expired
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires_at:
                print("⚠ Existing token has expired")
                return None

        return data
    except Exception as e:
        print(f"⚠ Could not load existing token: {e}")
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("WarcraftLogs User Token Authentication")
    print("=" * 60)

    # Parse command line arguments
    use_manual = "--manual" in sys.argv

    # Check for existing token
    existing = load_existing_token()
    if existing:
        print(f"\n✓ Found existing token (created: {existing.get('created_at', 'unknown')})")
        print(f"  Expires: {existing.get('expires_at', 'unknown')}")
        response = input("\nDo you want to get a new token anyway? (y/N): ")
        if response.lower() != "y":
            print(f"\n📋 Your existing token:")
            print(f"   {existing['access_token'][:50]}...")
            print(f"\nThe token is stored in your config file.")
            exit(0)

    # Get credentials from config
    client_id = config.get_wcl_client_id()
    client_secret = config.get_wcl_client_secret()

    if not client_id or not client_secret:
        print("\n❌ Missing credentials!")
        print("Please set the WCL client ID and secret in the app settings.")
        print(f"Config file location: {config.get_config_path()}")
        exit(1)

    print(f"\n✓ Using client ID: {client_id[:10]}...")
    print(f"✓ Using redirect URI: {REDIRECT_URI}")

    # Check redirect URI configuration
    print(f"\n⚠ IMPORTANT: Make sure your WarcraftLogs app has this redirect URI registered:")
    print(f"   {REDIRECT_URI}")
    print("\n   Configure at: https://www.warcraftlogs.com/api/clients")

    input("\nPress Enter when ready to continue...")

    try:
        if use_manual:
            token_data = manual_auth_flow(client_id, client_secret)
        else:
            token_data = authenticate(client_id, client_secret)

        print("\n" + "=" * 60)
        print("✅ Authentication successful!")
        print("=" * 60)
        print(f"\n📋 Your access token:")
        print(f"   {token_data['access_token'][:50]}...")
        print(f"\n📁 Token saved to:")
        print(f"   {TOKEN_STORAGE_PATH}")
        print(f"\n✓ Token has been saved to config automatically.")

        if token_data.get("expires_in"):
            hours = token_data["expires_in"] / 3600
            print(f"\n⏰ Token expires in: {hours:.1f} hours")

    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        print("\n💡 Try running with --manual flag to authenticate manually")
        exit(1)
