"""
Permission and Whitelist Validation Layer.
Ensures that only videos from approved creators or verified Creative Commons sources are processed.
"""
import argparse
import sys
from typing import Any, Dict, Optional, Tuple

import yt_dlp

from database.db import Database


class PermissionManager:
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def extract_video_info(self, url: str) -> Dict[str, Any]:
        """Extracts minimal metadata without downloading video stream."""
        opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info or {}

    def is_source_permitted(self, url_or_info: Any) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates if the content is approved by:
        1. Checking if the channel_id is marked approved in the local database.
        2. Checking if the video is officially marked with a Creative Commons (CC-BY) license.

        Returns: (is_permitted: bool, reason: str, metadata: dict)
        """
        if isinstance(url_or_info, str):
            info = self.extract_video_info(url_or_info)
        else:
            info = url_or_info

        channel_id = info.get("channel_id") or info.get("uploader_id") or ""
        channel_name = info.get("channel") or info.get("uploader") or "Unknown"
        license_str = str(info.get("license") or "")

        meta = {
            "youtube_id": info.get("id"),
            "title": info.get("title"),
            "channel_id": channel_id,
            "channel_name": channel_name,
            "license": license_str,
            "duration": info.get("duration", 0),
            "is_live": info.get("is_live", False),
        }

        # Check 1: Whitelist database check
        if channel_id and self.db.is_creator_approved(channel_id):
            creator = self.db.get_creator(channel_id)
            proof = creator.get("permission_proof", "Documented permission on file")
            return True, f"Approved creator: {channel_name} ({proof})", meta

        # Check 2: Creative Commons license check
        if "creative commons" in license_str.lower() or "cc-by" in license_str.lower():
            return True, f"Creative Commons License: {license_str}", meta

        return (
            False,
            f"Channel '{channel_name}' ({channel_id}) is not in approved whitelist and has no CC license.",
            meta,
        )

    def enforce_permission(self, url: str) -> Dict[str, Any]:
        """Strict gatekeeper: raises PermissionError if content is not approved."""
        permitted, reason, meta = self.is_source_permitted(url)
        if not permitted:
            raise PermissionError(f"[PERMISSION DENIED] {reason}")
        print(f"[PERMISSION GRANTED] {reason}")
        return meta


def main():
    parser = argparse.ArgumentParser(description="Manage approved creators & check video permissions")
    subparsers = parser.add_subparsers(dest="action")

    # Command: add
    add_parser = subparsers.add_parser("add", help="Add an approved creator")
    add_parser.add_argument("--channel-id", required=True, help="YouTube channel ID (e.g. UCxxxx)")
    add_parser.add_argument("--name", required=True, help="Creator display name / handle")
    add_parser.add_argument("--proof", required=True, help="Documentation proof (email date, DM, agreement)")
    add_parser.add_argument("--revoked", action="store_true", help="Set to revoke permission")

    # Command: list
    subparsers.add_parser("list", help="List all tracked creators")

    # Command: check
    check_parser = subparsers.add_parser("check", help="Check if a video URL is approved to process")
    check_parser.add_argument("url", help="YouTube video or stream URL")

    args = parser.parse_args()
    db = Database()
    mgr = PermissionManager(db)

    if args.action == "add":
        db.add_creator(
            channel_id=args.channel_id,
            channel_name=args.name,
            permission_proof=args.proof,
            approved=not args.revoked,
        )
        status = "REVOKED" if args.revoked else "APPROVED"
        print(f"Creator '{args.name}' ({args.channel_id}) successfully updated with status: {status}")

    elif args.action == "list":
        creators = db.list_creators()
        if not creators:
            print("No creators in database yet. Add one with: python -m modules.permissions add ...")
            return
        print(f"\n{'STATUS':<10} | {'CHANNEL NAME':<25} | {'CHANNEL ID':<26} | {'PROOF'}")
        print("-" * 85)
        for c in creators:
            st = "APPROVED" if c["approved"] else "REVOKED"
            print(f"{st:<10} | {c['channel_name']:<25} | {c['channel_id']:<26} | {c['permission_proof']}")
        print()

    elif args.action == "check":
        permitted, reason, meta = mgr.is_source_permitted(args.url)
        print(f"URL: {args.url}")
        print(f"Title: {meta.get('title')}")
        print(f"Channel: {meta.get('channel_name')} ({meta.get('channel_id')})")
        print(f"Permitted: {permitted}")
        print(f"Reason: {reason}")
        if not permitted:
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
