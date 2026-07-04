# Testing guide — data.world bulk downloader

## What this tool does
Backs up all data.world datasets **owned by the authenticated user** before the service
shuts down. Two independent scripts (pick either — same dataset list, different output):

| Script | Output | Needs |
|--------|--------|-------|
| `download_dataworld_api.py` | one `.zip` per dataset (all files + metadata bundle) | `requests` only |
| `download_dataworld.py` | extracted files + `metadata.json` per dataset | `requests` **and** `datadotworld` |

Both page through the API (`GET /v0/user/datasets/own`, 100/page), download each dataset
(`GET download.data.world/download/{owner}/{id}` for the zip script), rate-limit, log, and
exit non-zero if anything failed.

## Files in this handoff
- `download_dataworld_api.py` — REST/zip downloader (recommended; fewer dependencies)
- `download_dataworld.py` — SDK downloader (extracts individual files)
- `test_dataworld.py` — **offline** logic tests (no account/token needed)
- `DATAWORLD_DOWNLOAD_README.md` — end-user setup/run instructions
- `TESTING.md` — this file

---

## ⚠️ Before you start: the token is a secret
The scripts authenticate with a data.world **Client Token**, which grants full access to
that account. **Preferred:** the tester uses **their own** data.world account with a couple
of throwaway datasets. If you must use the owner's token, treat it like a password (don't
paste it into shared chat/tickets), and **regenerate/revoke it after testing**
(data.world → Settings → Integrations → Python → Manage).

---

## Test level 1 — Offline logic (no account, ~5 seconds)
Proves pagination, `owner/id` handling, atomic/validated downloads, resume, and filename
recovery without touching the network.

```bash
pip install requests
python3 test_dataworld.py
```
**Expect:** several `PASS ...` lines ending in `ALL TESTS PASSED`, exit code `0`.
If any line says `AssertionError` or the exit code is non-zero, that's a regression — stop and report.

## Test level 2 — Live preview / smoke test (real token, downloads nothing)
Confirms auth, pagination, and dataset enumeration against the real API.

```bash
export DW_AUTH_TOKEN="<token>"
python3 download_dataworld_api.py --list
```
**Expect:** `Found N datasets for <username>`, then a numbered list `owner/slug — title`.
- The count/names should match what the account actually owns (check the data.world web UI).
- **Pagination check:** if the account owns **more than 100** datasets, confirm *all* of them
  appear, not just 100 (this was a real bug — the old code stopped at 10).

## Test level 3 — Full download (real token, writes files)
```bash
# Zip variant (recommended):
python3 download_dataworld_api.py

# OR the SDK variant:
pip install datadotworld
python3 download_dataworld.py
```
Output goes to `~/Downloads/data.world_backup/<timestamp>/` with a `download_log.txt`.

**Verify:**
1. Exit code is `0` when everything succeeds (`echo $?`). If any dataset fails, exit is `1` —
   that is correct behavior, and the failures are listed in the log.
2. Every dataset the account owns produced output (compare against the `--list` count).
3. **Zip script:** each `.zip` opens and contains the dataset's files. No leftover `.part` files.
   A `.zip` that's actually an HTML page = bug (should have been rejected).
4. **SDK script:** files keep their real names/extensions (e.g. `Something.csv`, not
   `something` with no extension); each dataset dir has a `metadata.json` with a real
   `title`/`description` (not `"N/A"`/empty).

## Test level 4 — Resume (real token)
1. Start a full run and interrupt it partway (`Ctrl-C`).
2. Re-run pointing at the **same** directory:
   ```bash
   python3 download_dataworld_api.py ~/Downloads/data.world_backup/<the-timestamp-dir>
   ```
   **Expect:** already-finished datasets print `= owner/slug (already downloaded, skipped)`
   and are not re-downloaded; only the remaining ones are fetched.

---

## Edge cases worth checking
| Scenario | Expected |
|----------|----------|
| Empty / whitespace token (just press Enter at the prompt) | Exits with `Error: No API token provided.` |
| Expired / wrong token | `Error authenticating (check your token …)`, not a confusing "No datasets found" |
| Account with 0 owned datasets | `No datasets found` and exit `1` (nothing to back up) |
| Network drops mid-download | That dataset logged `✗`, no truncated `.zip` left behind, run continues |
| Account with >100 datasets | All datasets downloaded (pagination) |

## What to report back
For any failure, capture: the command run, the full console output, the exit code
(`echo $?`), the `download_log.txt`, Python version (`python3 --version`), and the OS.
**Never include the token in a bug report.**

## Known limitations (not bugs)
- `GET /v0/user/datasets/own` lists datasets the user **owns**. Datasets they only
  *contribute to* or *liked* are not included (by design). If those are wanted too, the
  endpoints `/v0/user/datasets/contributing` and `/v0/user/datasets/liked` exist.
- Requires network access to `api.data.world` and `download.data.world`.
- If data.world has already shut those hosts down, live tests (levels 2–4) can't run;
  the offline test (level 1) still validates the code logic.
