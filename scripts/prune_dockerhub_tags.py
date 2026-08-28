#!/usr/bin/env python3
"""Deletes all but the newest few full-semver tags from Docker Hub repositories.

Old version tags serve no one: launchers track `latest`, and rollbacks only ever
reach for the last release or two. Keeping the newest three and deleting the rest on
every publish stops the account from accumulating an image per release forever.

Only full ``major.minor.patch`` tags age out. ``latest`` and the shortened
``major`` / ``major.minor`` tags are never touched, because those are what running
installations and the Kayak configuration actually reference.

Usage:
    prune_dockerhub_tags.py [--keep N] repo [repo ...]

Credentials come from DOCKERHUB_USERNAME and DOCKERHUB_TOKEN. The token needs
"Read, Write & Delete" permissions; anything less fails with a message saying so
rather than silently leaving the tags in place.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

FULL_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def call(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"JWT {token}")
    with urllib.request.urlopen(request) as response:
        body = response.read().decode()
        return json.loads(body) if body.strip() else {}


def login() -> str:
    session = call(
        "https://hub.docker.com/v2/users/login",
        method="POST",
        payload={
            "username": os.environ["DOCKERHUB_USERNAME"],
            "password": os.environ["DOCKERHUB_TOKEN"],
        },
    )
    return session["token"]


def list_tags(repo: str, token: str) -> List[str]:
    tags: List[str] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100"
    while url:
        page = call(url, token=token)
        tags.extend(entry["name"] for entry in page.get("results", []))
        url = page.get("next")
    return tags


def prune(repo: str, keep: int, token: str) -> None:
    versions = sorted(
        (tag for tag in list_tags(repo, token) if FULL_VERSION.match(tag)),
        key=lambda version: tuple(int(part) for part in version.split(".")),
        reverse=True,
    )
    print(f"{repo}: keeping {versions[:keep]}")

    for tag in versions[keep:]:
        try:
            call(
                f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}/",
                method="DELETE",
                token=token,
            )
            print(f"deleted {repo}:{tag}")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                print(
                    "::error::The Docker Hub token cannot delete tags. "
                    "Regenerate it with Read, Write & Delete permissions "
                    "and update the DOCKERHUB_TOKEN secret (see RELEASING.md)."
                )
                sys.exit(1)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repos", nargs="+", help="Docker Hub repositories, owner/name")
    parser.add_argument("--keep", type=int, default=3, help="How many versions to keep")
    args = parser.parse_args()

    token = login()
    for repo in args.repos:
        prune(repo, args.keep, token)


if __name__ == "__main__":
    main()
