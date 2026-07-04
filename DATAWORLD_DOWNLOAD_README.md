# data.world Bulk Downloader

Two scripts for emergency bulk download before shutdown.

## Setup (5 min)

1. **Get API token:**
   - Go to https://data.world
   - Settings → Integrations → Python
   - Click "Manage" → Copy "Client Token"
   - This token is what lists your datasets (`GET /v0/user/datasets/own`) and downloads them.

2. **Install dependencies:**
   ```bash
   pip install datadotworld requests
   ```

3. **Set token in environment (optional but recommended):**
   ```bash
   export DW_AUTH_TOKEN="your_token_here"
   ```
   Or paste when prompted.

## Option 1: Download Raw Files (Recommended for most users)

```bash
python3 download_dataworld.py
```

**Saves:**
- Individual files extracted from each dataset (original filenames/extensions preserved)
- Metadata JSON for each dataset (title/description from the API listing)
- Organized by dataset ID (`owner_slug/`)

**Best for:** Preserving raw data files, smaller datasets

## Option 2: Download .zip Files (Faster for large datasets)

```bash
python3 download_dataworld_api.py
```

**Saves:**
- Complete .zip files per dataset (all files + metadata bundle), via `GET download.data.world/download/{owner}/{id}`
- Smallest bandwidth footprint
- Can extract later

**Best for:** Speed, large datasets, bandwidth-constrained

## Output

Both scripts save to:
```
~/Downloads/data.world_backup/YYYYMMDD_HHMMSS/
```

With log file at:
```
~/Downloads/data.world_backup/YYYYMMDD_HHMMSS/download_log.txt
```

## Preview before downloading

Run either script with `--list` to enumerate every dataset that would be downloaded
(hits the real API with your token, downloads nothing) — a quick way to confirm auth,
pagination, and the dataset count before committing to a full backup:

```bash
python3 download_dataworld_api.py --list
```

## Notes

- Scripts page through **all** your datasets (`GET /v0/user/datasets/own`, 100/page)
- Rate limited (0.3–0.5s between requests)
- Includes error logging; non-zero exit code if any dataset fails
- Each run creates a fresh timestamped directory by default

## Troubleshooting

**"Error authenticating"** → Token is missing/expired; regenerate the Client Token

**Slow speed** → Normal for large datasets; script will continue

**Need to resume?** → Pass the existing output directory as an argument, e.g.
`python3 download_dataworld_api.py ~/Downloads/data.world_backup/20260704_120000` —
datasets whose files already exist are skipped.

---

For bulk data preservation on Arch Linux with your setup, consider also:
- Parallel downloads with `GNU parallel` wrapper if needed
- MD5 checksums for validation (can add to script)
- Compress output with `tar.xz` for archive storage
