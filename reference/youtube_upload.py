#!/usr/bin/env python3
"""
Upload a vertical clip to YouTube (Shorts) via YouTube Data API v3.

Setup:
  pip install google-api-python-client google-auth-oauthlib
  Put OAuth "Desktop app" credentials next to this file as client_secret.json
  First run opens a browser to log in; token is saved in token.pickle.

Usage:
  python youtube_upload.py clips/clip_1.mp4 --title "Epic GTA 6 moment" \
      --credit "@CreatorName" --tags gta6 gta vicecity
"""
import argparse
import os
import pickle

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
GAMING_CATEGORY = "20"


def get_service():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


def upload(path, title, description, tags, privacy="public"):
    yt = get_service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": GAMING_CATEGORY,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"uploading... {int(status.progress() * 100)}%")
    url = f"https://youtube.com/shorts/{resp['id']}"
    print("done:", url)
    return resp["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--credit", default="")
    ap.add_argument("--desc", default="")
    ap.add_argument("--tags", nargs="*", default=["gta6", "gta"])
    ap.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    args = ap.parse_args()

    desc = args.desc
    if args.credit:
        desc += f"\n\nCredit: {args.credit} (used with permission)"
    desc += "\n\n#Shorts #GTA6"
    upload(args.file, args.title, desc, args.tags, args.privacy)


if __name__ == "__main__":
    main()
