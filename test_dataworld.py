#!/usr/bin/env python3
"""
Offline logic tests for the data.world downloaders.

Runs with NO data.world account and NO token — it replaces the network layer
(`requests`) and the `datadotworld` SDK with fakes, then drives the real
functions from both scripts and asserts on their behaviour.

Verifies the bugs that were fixed:
  - dataset list is paginated (must accumulate all pages, not just the first 10)
  - each dataset key is owner/id (owner + slug), not the bare slug
  - downloads are atomic (.part -> rename) and validated (must be a real zip)
  - a finished dataset is skipped on re-run (resume)
  - the SDK script forwards the auth token and saves files with real extensions

Requires only: pip install requests
Usage:  python3 test_dataworld.py     (prints PASS lines; exits non-zero on failure)
"""

import sys
import types
import tempfile
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename, fake_modules=None):
    for modname, mod in (fake_modules or {}).items():
        sys.modules[modname] = mod
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Shared fakes
# --------------------------------------------------------------------------
class FakeExceptions:
    RequestException = Exception


class FakeResp:
    def __init__(self, json_data=None, chunks=None, headers=None, ok=True):
        self._json = json_data
        self._chunks = chunks or []
        self.headers = headers or {}
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise FakeExceptions.RequestException("HTTP error")

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        for c in self._chunks:
            yield c


def make_paginated_get(counter):
    """A fake requests.get that returns a user, two pages of datasets, and a zip."""
    def fake_get(url, headers=None, params=None, stream=False, timeout=None, allow_redirects=False):
        if url.endswith('/user'):
            return FakeResp(json_data={'id': 'testuser'})
        if url.endswith('/user/datasets/own'):
            counter['pages'] += 1
            if not (params or {}).get('next'):
                return FakeResp(json_data={
                    'count': 3,
                    'records': [
                        {'owner': 'testuser', 'id': 'alpha', 'title': 'Alpha', 'description': 'a'},
                        {'owner': 'testuser', 'id': 'beta', 'title': 'Beta', 'description': 'b'},
                    ],
                    'nextPageToken': 'PAGE2',
                })
            return FakeResp(json_data={  # no nextPageToken -> last page
                'count': 3,
                'records': [{'owner': 'otherorg', 'id': 'gamma', 'title': 'Gamma', 'description': 'g'}],
            })
        if '/download/' in url:
            counter['downloads'] += 1
            body = b'PK\x03\x04' + b'\x00' * 64  # valid zip magic
            return FakeResp(chunks=[body],
                            headers={'content-type': 'application/zip',
                                     'content-length': str(len(body))})
        raise AssertionError(f"unexpected URL {url}")
    return fake_get


# --------------------------------------------------------------------------
# Tests for download_dataworld_api.py (REST / zip)
# --------------------------------------------------------------------------
def test_api_script():
    counter = {'pages': 0, 'downloads': 0}
    fake_requests = types.SimpleNamespace(get=make_paginated_get(counter), exceptions=FakeExceptions)
    api = _load("dwapi", "download_dataworld_api.py", {"requests": fake_requests})
    api.requests = fake_requests

    datasets, username = api.list_datasets("faketoken")
    keys = [f"{d['owner']}/{d['id']}" for d in datasets]
    assert username == 'testuser', username
    assert keys == ['testuser/alpha', 'testuser/beta', 'otherorg/gamma'], keys
    assert counter['pages'] == 2, "should fetch both pages"
    print("PASS [api] pagination accumulates all 3 datasets across 2 pages")

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        ok, msg = api.download_dataset_zip('otherorg', 'gamma', out, "faketoken")
        zf = out / "otherorg_gamma.zip"
        assert ok and zf.exists() and zf.read_bytes()[:2] == b'PK', msg
        assert not (out / "otherorg_gamma.zip.part").exists(), ".part not cleaned"
        print("PASS [api] download is atomic + zip-validated:", msg)

        before = counter['downloads']
        ok2, msg2 = api.download_dataset_zip('otherorg', 'gamma', out, "faketoken")
        assert ok2 and 'skipped' in msg2 and counter['downloads'] == before, msg2
        print("PASS [api] resume skips an already-downloaded dataset:", msg2)

        api.requests = types.SimpleNamespace(
            get=lambda *a, **k: FakeResp(chunks=[b'<html>login</html>'],
                                         headers={'content-type': 'text/html'}),
            exceptions=FakeExceptions)
        ok3, msg3 = api.download_dataset_zip('x', 'y', out, "faketoken")
        assert not ok3 and 'not a zip' in msg3 and not (out / "x_y.zip").exists(), msg3
        print("PASS [api] HTML/redirect page rejected, never saved as .zip:", msg3)


# --------------------------------------------------------------------------
# Tests for download_dataworld.py (SDK)
# --------------------------------------------------------------------------
def test_sdk_script():
    captured = {'auth_token': None}

    class FakeLocalDataset:
        raw_data = {'datadotworldbballstats': b'a,b\n1,2\n'}  # key is a slug, no extension
        def describe(self):
            return {'resources': [{'name': 'datadotworldbballstats',
                                   'path': 'original/DataDotWorldBBallStats.csv'}]}

    def fake_load_dataset(key, auth_token=None, force_update=False):
        captured['auth_token'] = auth_token
        captured['key'] = key
        return FakeLocalDataset()

    fake_dw = types.ModuleType('datadotworld')
    fake_dw.load_dataset = fake_load_dataset
    counter = {'pages': 0, 'downloads': 0}
    fake_requests = types.SimpleNamespace(get=make_paginated_get(counter), exceptions=FakeExceptions)

    sdk = _load("dwsdk", "download_dataworld.py", {"datadotworld": fake_dw, "requests": fake_requests})
    sdk.requests = fake_requests
    sdk.dw = fake_dw

    datasets, username = sdk.list_user_datasets("faketoken")
    keys = [f"{d['owner']}/{d['id']}" for d in datasets]
    assert username == 'testuser' and keys == ['testuser/alpha', 'testuser/beta', 'otherorg/gamma'], keys
    print("PASS [sdk] pagination + owner/id join match the REST script")

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        ok, msg = sdk.download_dataset('testuser/alpha', 'Alpha', 'desc', out, "faketoken")
        assert ok, msg
        # token was actually forwarded to the SDK (the old code used a no-op dw.config)
        assert captured['auth_token'] == 'faketoken', captured
        assert captured['key'] == 'testuser/alpha', captured
        # file saved under its REAL name/extension, not the slug
        saved = out / "testuser_alpha" / "files" / "DataDotWorldBBallStats.csv"
        assert saved.exists() and saved.read_bytes() == b'a,b\n1,2\n', "extension not recovered"
        import json
        meta = json.loads((out / "testuser_alpha" / "metadata.json").read_text())
        assert meta['title'] == 'Alpha' and meta['files'] == ['DataDotWorldBBallStats.csv'], meta
        print("PASS [sdk] token forwarded, real filename recovered, metadata populated:", msg)

        ok2, msg2 = sdk.download_dataset('testuser/alpha', 'Alpha', 'desc', out, "faketoken")
        assert ok2 and 'skipped' in msg2, msg2
        print("PASS [sdk] resume skips an already-downloaded dataset:", msg2)


if __name__ == '__main__':
    test_api_script()
    test_sdk_script()
    print("\nALL TESTS PASSED")
