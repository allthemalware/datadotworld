#!/usr/bin/env python3
"""
Direct REST API bulk downloader - downloads each dataset as a .zip.
Faster for large datasets. Requires: pip install requests

Usage:
  python3 download_dataworld_api.py               # download all datasets you own
  python3 download_dataworld_api.py --list        # list what would be downloaded, don't download
  python3 download_dataworld_api.py <output_dir>  # resume into an existing dir (skips finished .zips)
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime
import requests

API_BASE = 'https://api.data.world/v0'
DOWNLOAD_BASE = 'https://download.data.world'   # hit the download host directly so the
                                                # Authorization header survives (requests
                                                # strips it across a cross-host redirect)
PAGE_LIMIT = 100          # API max page size
REQUEST_TIMEOUT = 30      # seconds for metadata calls
DOWNLOAD_TIMEOUT = 600    # seconds for a full-dataset zip


def get_token():
    """Get API token from environment or prompt."""
    token = os.environ.get('DW_AUTH_TOKEN')
    if not token:
        print("Get token from: https://data.world > Settings > Integrations > Python > Manage > Client Token")
        token = input("Paste API token: ").strip()
    if not token:
        sys.exit("Error: No API token provided.")
    return token


def list_datasets(token):
    """Fetch ALL datasets the authenticated user owns, following pagination."""
    headers = {'Authorization': f'Bearer {token}'}

    # Identify the caller (used only for logging).
    try:
        u = requests.get(f'{API_BASE}/user', headers=headers, timeout=REQUEST_TIMEOUT)
        u.raise_for_status()
        username = u.json().get('id', '?')
    except requests.exceptions.RequestException as e:
        sys.exit(f"Error authenticating (check your token / it may be expired): {e}")

    datasets = []
    next_token = None
    while True:
        params = {'limit': PAGE_LIMIT}
        if next_token:
            params['next'] = next_token
        try:
            resp = requests.get(f'{API_BASE}/user/datasets/own',
                                headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching dataset list: {e}")
            break

        body = resp.json()
        datasets.extend(body.get('records', []))
        next_token = body.get('nextPageToken')
        if not next_token:
            break

    return datasets, username


def download_dataset_zip(owner, ds_id, output_dir, token):
    """Download a single dataset as a .zip via GET /download/{owner}/{id}."""
    key = f"{owner}/{ds_id}"
    headers = {'Authorization': f'Bearer {token}'}
    safe_name = f"{owner}_{ds_id}".replace('/', '_')
    filepath = output_dir / f"{safe_name}.zip"

    # Resume support: skip datasets already fully downloaded.
    if filepath.exists() and filepath.stat().st_size > 0:
        return True, f"= {key} (already downloaded, skipped)"

    tmp = output_dir / f"{safe_name}.zip.part"
    url = f'{DOWNLOAD_BASE}/download/{owner}/{ds_id}'

    try:
        resp = requests.get(url, headers=headers, stream=True,
                            timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()

        # Guard against an auth/redirect page returned with HTTP 200.
        ctype = resp.headers.get('content-type', '').lower()
        if 'html' in ctype:
            return False, f"✗ {key}: got {ctype!r}, not a zip (auth/redirect page?)"

        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        magic = b''

        with open(tmp, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                if chunk:
                    if not magic:
                        magic = chunk[:4]
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        pct = (downloaded / total_size) * 100
                        print(f"  {downloaded/1e6:.1f}MB / {total_size/1e6:.1f}MB ({pct:.0f}%)", end='\r')

        # Validate it is actually a zip before promoting the file.
        if magic[:2] != b'PK':
            tmp.unlink(missing_ok=True)
            return False, f"✗ {key}: downloaded content is not a zip (bad magic bytes)"

        os.replace(tmp, filepath)  # atomic: a partial download never looks complete
        size_mb = filepath.stat().st_size / 1e6
        return True, f"✓ {key} ({size_mb:.1f}MB)"

    except requests.exceptions.RequestException as e:
        tmp.unlink(missing_ok=True)  # never leave a truncated .part behind
        return False, f"✗ {key}: {e}"


def main():
    argv = sys.argv[1:]
    list_only = '--list' in argv
    positionals = [a for a in argv if not a.startswith('-')]

    print("=== data.world Direct Downloader ===\n")

    token = get_token()

    if positionals:
        output_dir = Path(positionals[0]).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path.home() / "Downloads" / "data.world_backup" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "download_log.txt"

    print(f"Saving to: {output_dir}\n")
    print("Fetching dataset list...")

    datasets, username = list_datasets(token)
    if not datasets:
        print("No datasets found (or the list request failed).")
        return 1

    print(f"Found {len(datasets)} datasets for {username}\n")

    if list_only:
        for idx, ds in enumerate(datasets, 1):
            print(f"[{idx}/{len(datasets)}] {ds.get('owner')}/{ds.get('id')}  —  {ds.get('title', '')}")
        print(f"\n(--list) Would download {len(datasets)} datasets. Nothing was downloaded.")
        return 0

    successful = 0
    failed = 0

    with open(log_file, 'w') as log:
        log.write(f"Started: {datetime.now()}\nUser: {username}\nTotal: {len(datasets)}\n\n")

        for idx, ds in enumerate(datasets, 1):
            owner = ds.get('owner')
            ds_id = ds.get('id')
            if not owner or not ds_id:
                msg = f"✗ skipped malformed record: {ds!r}"
                print(msg)
                log.write(msg + "\n")
                failed += 1
                continue

            print(f"[{idx}/{len(datasets)}] {owner}/{ds_id}...", end=" ", flush=True)
            success, msg = download_dataset_zip(owner, ds_id, output_dir, token)
            print(f"\n{msg}")
            log.write(msg + "\n")

            if success:
                successful += 1
            else:
                failed += 1

            time.sleep(0.3)  # gentle rate limiting

        summary = f"\nComplete: {successful} succeeded, {failed} failed, {len(datasets)} total"
        print(summary)
        print(f"Location: {output_dir}")
        log.write(summary + "\n")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
