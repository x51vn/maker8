# Dropbox Upload TypeError Fix – 2026-04-14

## Problem

UPLOAD_DROPBOX stage failed on all retry attempts (5 retries exhausted) with:

```
TypeError: expected request_binary as binary type, got <class '_io.BufferedReader'>
```

Job `a5abc35e-e928-4a43-b32a-ce7624d4a0b2` rendered a 69.82 MB video successfully
but could not upload it to Dropbox. The error is deterministic – retrying is futile
because the root cause is a code-level incompatibility with the Dropbox SDK.

## Root Cause

**`dropbox_client.py::_simple_upload()`** passed an open file handle (`BufferedReader`)
to `files_upload()`:

```python
# BEFORE (broken)
with open(local_path, "rb") as fh:
    result = self._dbx.files_upload(fh, remote_path, mode=WriteMode.overwrite)
```

The **Dropbox Python SDK v12.0.2** changed its `request_json_string()` method to
perform an explicit type check:

```python
# dropbox/dropbox_client.py line ~538
if not isinstance(request_binary, (six.binary_type, type(None))):
    raise TypeError('expected request_binary as binary type, got %s' % type(request_binary))
```

`six.binary_type` is `bytes`. A `BufferedReader` is not `bytes`, so the check fails
immediately – before any HTTP call is made (note `duration_ms: 2-4` in logs).

The `_session_upload()` path was **not affected** because it already calls `fh.read()`
to get `bytes` chunks before passing them to the SDK.

## Fix

Read the entire file into memory as `bytes` before calling `files_upload()`:

```python
# AFTER (fixed)
data = local_path.read_bytes()
result = self._dbx.files_upload(data, remote_path, mode=WriteMode.overwrite)
```

This is safe because `_simple_upload()` is only used for files ≤ 150 MiB
(`_UPLOAD_LIMIT`). Files larger than that use `_session_upload()` with chunked reads.

### Changed file

- `src/maker8/services/dropbox_client.py` – `_simple_upload()` method

## Deployment

- **Image:** `docker.x51.vn/x-ai/maker8:20260414.2150`
- **Host:** worker-z440
- **Status:** deployed and healthy
