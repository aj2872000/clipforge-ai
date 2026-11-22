import os
import json
import time
import math
import logging
import urllib.parse
from typing import Dict, Any, Optional, List, Callable
import requests

logger = logging.getLogger("shorts_creator.youtube")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
TOKENS_PATH = os.path.join(STORAGE_DIR, "youtube_tokens.json")

# Standard YouTube Data API v3 OAuth & Upload endpoints
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_RESUMABLE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

# Standard chunk size for resumable upload (4MB)
CHUNK_SIZE = 4 * 1024 * 1024

def get_oauth_config() -> Dict[str, str]:
    """Retrieve OAuth client configuration from environment or .env."""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/oauth2callback")

    # Also check if client_secrets.json exists in workspace
    secrets_file = os.path.join(BASE_DIR, "client_secrets.json")
    if (not client_id or not client_secret) and os.path.exists(secrets_file):
        try:
            with open(secrets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                web = data.get("web") or data.get("installed") or {}
                client_id = client_id or web.get("client_id", "")
                client_secret = client_secret or web.get("client_secret", "")
                uris = web.get("redirect_uris", [])
                if uris:
                    redirect_uri = uris[0]
        except Exception as e:
            logger.warning(f"Error parsing client_secrets.json: {e}")

    return {
        "client_id": client_id.strip() if client_id else "",
        "client_secret": client_secret.strip() if client_secret else "",
        "redirect_uri": redirect_uri.strip() if redirect_uri else "http://localhost:8000/api/youtube/oauth2callback"
    }

def load_tokens() -> Optional[Dict[str, Any]]:
    """Load stored OAuth tokens from disk."""
    if not os.path.exists(TOKENS_PATH):
        return None
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading YouTube tokens: {e}")
        return None

def save_tokens(tokens: Dict[str, Any]) -> None:
    """Save OAuth tokens securely to disk."""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    try:
        with open(TOKENS_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving YouTube tokens: {e}")

def get_authorization_url(state: Optional[str] = None) -> str:
    """Generate the Google OAuth 2.0 authorization URL."""
    cfg = get_oauth_config()
    if not cfg["client_id"]:
        raise ValueError("Missing YOUTUBE_CLIENT_ID. Please configure it in .env or settings.")

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true"
    }
    if state:
        params["state"] = state

    return f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"

def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """Exchange authorization code for access and refresh tokens."""
    cfg = get_oauth_config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise ValueError("Missing YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET.")

    payload = {
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code"
    }

    resp = requests.post(OAUTH_TOKEN_URL, data=payload, timeout=15)
    if resp.status_code != 200:
        logger.error(f"Failed to exchange code: {resp.text}")
        raise RuntimeError(f"Google OAuth token exchange failed: {resp.text}")

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)
    expires_at = time.time() + float(expires_in)

    token_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "token_type": data.get("token_type", "Bearer"),
        "scope": data.get("scope", "")
    }

    # Fetch channel information
    channel_info = fetch_channel_info(access_token)
    if channel_info:
        token_data.update(channel_info)

    save_tokens(token_data)
    return token_data

def refresh_access_token() -> str:
    """Refresh the access token using the stored refresh token."""
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise RuntimeError("No refresh token available. YouTube account must be reconnected.")

    cfg = get_oauth_config()
    payload = {
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token"
    }

    resp = requests.post(OAUTH_TOKEN_URL, data=payload, timeout=15)
    if resp.status_code != 200:
        logger.error(f"Failed to refresh access token: {resp.text}")
        raise RuntimeError(f"Failed to refresh YouTube token: {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    tokens["access_token"] = access_token
    tokens["expires_at"] = time.time() + float(expires_in)
    save_tokens(tokens)
    return access_token

def ensure_valid_token() -> str:
    """Return a valid access token, automatically refreshing if close to expiry."""
    tokens = load_tokens()
    if not tokens:
        raise RuntimeError("YouTube account is not connected.")

    # Refresh if expired or expiring within 90 seconds
    if time.time() >= tokens.get("expires_at", 0) - 90:
        logger.info("Access token expired or expiring soon. Refreshing...")
        return refresh_access_token()

    return tokens["access_token"]

def fetch_channel_info(access_token: str) -> Optional[Dict[str, Any]]:
    """Fetch user's YouTube channel details."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(
            f"{YOUTUBE_CHANNELS_URL}?part=snippet&mine=true",
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                snippet = items[0].get("snippet", {})
                return {
                    "channel_id": items[0].get("id", ""),
                    "channel_title": snippet.get("title", "My YouTube Channel"),
                    "channel_handle": snippet.get("customUrl", ""),
                    "channel_thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", "")
                }
    except Exception as e:
        logger.warning(f"Could not retrieve channel info: {e}")
    return None

def get_connection_status() -> Dict[str, Any]:
    """Return public YouTube connection state for UI."""
    cfg = get_oauth_config()
    has_credentials = bool(cfg["client_id"] and cfg["client_secret"])
    tokens = load_tokens()

    if not tokens or not tokens.get("access_token"):
        return {
            "connected": False,
            "has_credentials": has_credentials,
            "channel_title": None,
            "channel_handle": None,
            "channel_thumbnail": None
        }

    return {
        "connected": True,
        "has_credentials": has_credentials,
        "channel_id": tokens.get("channel_id"),
        "channel_title": tokens.get("channel_title", "Connected Channel"),
        "channel_handle": tokens.get("channel_handle", ""),
        "channel_thumbnail": tokens.get("channel_thumbnail", "")
    }

def disconnect() -> bool:
    """Disconnect connected YouTube account."""
    if os.path.exists(TOKENS_PATH):
        try:
            os.remove(TOKENS_PATH)
            return True
        except Exception as e:
            logger.error(f"Error deleting tokens file: {e}")
            return False
    return True

def resumable_upload_video(
    file_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    category_id: str = "24",
    privacy_status: str = "private",
    made_for_kids: bool = False,
    progress_callback: Optional[Callable[[int, int, int], None]] = None
) -> Dict[str, Any]:
    """
    Upload a local video file to YouTube via the official Resumable Upload protocol.
    Streams in 4MB chunks so large 1080p/4K files never time out or consume excess memory.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        raise ValueError("Video file is empty.")

    # 1. Obtain valid access token
    access_token = ensure_valid_token()

    # Clean & validate metadata
    clean_title = title.strip()[:100]
    clean_desc = (description or "").strip()[:5000]

    # Clean tags: max 500 total chars
    formatted_tags: List[str] = []
    total_tags_len = 0
    if tags:
        for t in tags:
            tag_clean = t.replace("#", "").strip()
            if tag_clean and len(tag_clean) + total_tags_len < 480:
                formatted_tags.append(tag_clean)
                total_tags_len += len(tag_clean) + 1

    metadata_payload = {
        "snippet": {
            "title": clean_title,
            "description": clean_desc,
            "tags": formatted_tags,
            "categoryId": str(category_id or "24"),
            "defaultLanguage": "en"
        },
        "status": {
            "privacyStatus": privacy_status.lower() if privacy_status.lower() in ["private", "unlisted", "public"] else "private",
            "selfDeclaredMadeForKids": bool(made_for_kids),
            "embeddable": True
        }
    }

    # Step 1: Initialize Resumable Upload Session
    init_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(file_size),
        "X-Upload-Content-Type": "video/mp4"
    }

    logger.info(f"Initiating YouTube Resumable Upload for '{clean_title}' ({file_size / (1024*1024):.1f} MB)...")
    init_resp = requests.post(
        YOUTUBE_RESUMABLE_UPLOAD_URL,
        headers=init_headers,
        json=metadata_payload,
        timeout=30
    )

    if init_resp.status_code not in (200, 201):
        # Check if auth error
        if init_resp.status_code == 401:
            logger.info("Session init 401: Refreshing token and retrying...")
            access_token = refresh_access_token()
            init_headers["Authorization"] = f"Bearer {access_token}"
            init_resp = requests.post(
                YOUTUBE_RESUMABLE_UPLOAD_URL,
                headers=init_headers,
                json=metadata_payload,
                timeout=30
            )

        if init_resp.status_code not in (200, 201):
            logger.error(f"Failed to initiate upload session ({init_resp.status_code}): {init_resp.text}")
            raise RuntimeError(f"YouTube upload initialization failed ({init_resp.status_code}): {init_resp.text}")

    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube API did not return an upload session URL in Location header.")

    logger.info("Resumable upload session created. Beginning chunked streaming...")

    # Step 2: Stream Video Chunks
    bytes_uploaded = 0
    with open(file_path, "rb") as f:
        while bytes_uploaded < file_size:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break

            chunk_len = len(chunk)
            start_byte = bytes_uploaded
            end_byte = start_byte + chunk_len - 1

            chunk_headers = {
                "Content-Length": str(chunk_len),
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                "Content-Type": "video/mp4"
            }

            upload_resp = requests.put(upload_url, headers=chunk_headers, data=chunk, timeout=60)

            if upload_resp.status_code in (200, 201):
                # Upload completely finished!
                bytes_uploaded = file_size
                if progress_callback:
                    progress_callback(100, file_size, file_size)

                result_data = upload_resp.json()
                video_id = result_data.get("id")
                video_url = f"https://youtu.be/{video_id}"
                logger.info(f"✓ Video successfully uploaded to YouTube! ID: {video_id} -> {video_url}")
                return {
                    "video_id": video_id,
                    "video_url": video_url,
                    "title": clean_title,
                    "privacy_status": privacy_status,
                    "data": result_data
                }

            elif upload_resp.status_code == 308:
                # 308 Resume Incomplete: Chunk received, continue
                bytes_uploaded = end_byte + 1
                progress_pct = min(99, int((bytes_uploaded / file_size) * 100))
                if progress_callback:
                    progress_callback(progress_pct, bytes_uploaded, file_size)
                logger.info(f"Uploading '{clean_title}': {progress_pct}% ({bytes_uploaded / (1024*1024):.1f}/{file_size / (1024*1024):.1f} MB)")

            else:
                logger.error(f"Upload chunk error ({upload_resp.status_code}): {upload_resp.text}")
                raise RuntimeError(f"Chunk upload failed with HTTP {upload_resp.status_code}: {upload_resp.text}")

    raise RuntimeError("Upload ended without receiving final YouTube video resource.")
