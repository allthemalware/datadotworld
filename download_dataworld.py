#!/usr/bin/env python3
"""
Bulk downloader for data.world datasets (Python SDK variant).
Extracts individual raw files + a metadata.json per dataset.
Requires: pip install datadotworld requests

Usage:
  python3 download_dataworld.py               # download all datasets you own
  python3 download_dataworld.py --list        # list what would be downloaded, don't download
  python3 download_dataworld.py <output_dir>  # resume into an existing dir (skips finished datasets)
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
import datadotworld as dw
import requests

API_BASE = 'https://api.data.world/v0'
PAGE_LIMIT = 100          # API max page size
REQUEST_TIMEOUT = 30      # seconds for metadata calls


def setup_auth():
    """Get API token from environment or user input."""
    token = os.environ.get('DW_AUTH_TOKEN')
    if not token:
        print("No DW_AUTH_TOKEN found in environment.")
        print("Get your token from: https://data.world > Settings > Integrations > Python > Manage > Client Token")
        token = input("Paste your API token: ").strip()
    if not token:
        sys.exit("Error: No API token provided.")
    return token


def list_user_datasets(token):
    """Fetch ALL datasets the authenticated user owns, following pagination."""
    headers = {'Authorization': f'Bearer {token}'}

    try:
        user_resp = requests.get(f'{API_BASE}/user', headers=headers, timeout=REQUEST_TIMEOUT)
        user_resp.raise_for_status()
        username = user_resp.json().get('id')
        if not username:
            print("Error: authenticated but /user response had no 'id'.")
            return [], None
    except requests.exceptions.RequestException as e:
        print(f"Error authenticating (check your token / it may be expired): {e}")
        return [], None

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


def _resource_filenames(dataset):
    """Map each raw_data resource name -> its original filename (with extension)."""
    mapping = {}
    try:
        desc = dataset.describe()
    except Exception:
        return mapping
    for res in desc.get('resources', []):
        name = res.get('name')
        path = res.get('path')
        if isinstance(path, list):
            path = path[0] if path else None
        if name and path:
            mapping[name] = os.path.basename(path)  # basename guards against path traversal
    return mapping


def download_dataset(dataset_id, title, description, output_path, token):
    """Download a single dataset using the Python SDK."""
    dataset_dir = output_path / dataset_id.replace('/', '_')
    files_dir = dataset_dir / "files"

    # Resume support: skip datasets already downloaded.
    if (dataset_dir / "metadata.json").exists() and files_dir.exists() and any(files_dir.iterdir()):
        return True, f"= {dataset_id} (already downloaded, skipped)"

    try:
        # Pass the token explicitly; dw.config.api_token does NOT exist and would be a no-op.
        dataset = dw.load_dataset(dataset_id, auth_token=token, force_update=True)

        dataset_dir.mkdir(parents=True, exist_ok=True)
        files_dir.mkdir(exist_ok=True)

        # Save raw files under their real filenames (raw_data keys are slugified resource
        # names without extensions, so recover the original name from describe()).
        name_map = _resource_filenames(dataset)
        written = 0
        for res_name, content in dataset.raw_data.items():
            filename = name_map.get(res_name) or res_name
            filename = os.path.basename(filename) or res_name
            with open(files_dir / filename, 'wb') as f:
                f.write(content)
            written += 1

        # Save metadata (title/description come from the REST record, not the SDK object,
        # which exposes neither attribute).
        metadata = {
            'id': dataset_id,
            'title': title,
            'description': description,
            'files': [name_map.get(k) or k for k in dataset.raw_data.keys()],
        }
        with open(dataset_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        if written == 0:
            return True, f"⚠ {dataset_id}: metadata only, 0 data files"
        return True, f"✓ {dataset_id} ({written} files)"
    except Exception as e:
        return False, f"✗ {dataset_id}: {e}"


def main():
    argv = sys.argv[1:]
    list_only = '--list' in argv
    positionals = [a for a in argv if not a.startswith('-')]

    print("=== data.world Bulk Downloader ===\n")

    token = setup_auth()

    if positionals:
        output_dir = Path(positionals[0]).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path.home() / "Downloads" / "data.world_backup" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "download_log.txt"

    print(f"Output directory: {output_dir}\n")
    print("Fetching your datasets...")

    datasets, username = list_user_datasets(token)
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
    start_time = time.time()

    with open(log_file, 'w') as log:
        log.write(f"Download started: {datetime.now()}\n")
        log.write(f"Username: {username}\n")
        log.write(f"Total datasets: {len(datasets)}\n\n")

        for idx, ds in enumerate(datasets, 1):
            owner = ds.get('owner')
            slug = ds.get('id')
            if not owner or not slug:
                msg = f"✗ skipped malformed record: {ds!r}"
                print(msg)
                log.write(msg + "\n")
                failed += 1
                continue

            dataset_id = f"{owner}/{slug}"
            title = ds.get('title', 'N/A')
            description = ds.get('description', '')

            print(f"[{idx}/{len(datasets)}] Downloading {dataset_id}...", end=" ", flush=True)
            success, msg = download_dataset(dataset_id, title, description, output_dir, token)
            print(msg)
            log.write(msg + "\n")

            if success:
                successful += 1
            else:
                failed += 1

            time.sleep(0.5)  # gentle rate limiting

    elapsed = time.time() - start_time
    summary = f"""
=== DOWNLOAD COMPLETE ===
Successful: {successful}
Failed: {failed}
Total: {len(datasets)}
Time: {elapsed:.1f}s

Output: {output_dir}
Log: {log_file}
"""
    print(summary)
    with open(log_file, 'a') as log:
        log.write(f"\n{summary}")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
