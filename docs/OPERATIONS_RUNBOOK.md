# Maker8 – Operations Runbook

> Quick-reference guide for operators managing maker8 in production.

---

## 1. Health Checks

### Probe files

| File | Purpose | Healthy |
|------|---------|---------|
| `/tmp/maker8_live` | Liveness | File exists |
| `/tmp/maker8_ready` | Readiness | File exists |
| `/tmp/maker8_status.json` | Full status snapshot | JSON readable |

### Docker health check

```bash
docker exec <container> test -f /tmp/maker8_ready
```

### Confirm Kafka connectivity

```bash
# Inside the container or on the worker host
docker logs <container> 2>&1 | grep -i "consumer.started\|consumer.subscribed"
```

If the consumer never logs `consumer.started`, Kafka may be unreachable.
Check `MAKER8_KAFKA_BOOTSTRAP_SERVERS` and network ACLs.

---

## 2. Common Incidents

### 2.1 DLQ messages accumulating

**Symptom**: Messages appearing in `video.render.dlq.v1`.

**Diagnosis**:
```bash
# Read DLQ messages (using kcat / kafkacat)
kcat -b <broker> -t video.render.dlq.v1 -C -e -o beginning | jq '.failed_stage, .last_error'
```

**Triage by `failed_stage`**:

| Stage | Likely cause | Action |
|-------|-------------|--------|
| `VALIDATE` | Malformed input from editor8 | Check editor8 pipeline; input cannot be retried |
| `RESOLVE_ASSETS` | YouTube URL expired or invalid | Check source URLs; update in editor8 and re-send |
| `DOWNLOAD` | Network issue, HTTP 5xx, disk full | Check network; check disk space (`df -h`) |
| `NORMALIZE` | FFmpeg crash, corrupt media | Check FFmpeg logs; verify downloaded asset is valid; see `docs/MAKER8_NVENC_FALLBACK_INVESTIGATION_GUIDE.md` for NVENC fallback triage |
| `TTS` | All TTS credentials exhausted | See §2.3 — TTS credential issues |
| `RENDER` | MoviePy error, all scenes skipped | Check if all assets failed; review render_spec |
| `UPLOAD_DROPBOX` | Auth expired, rate limit | See §2.4 — Dropbox issues |
| `EMIT_RESULT` | Kafka producer failure | Check Kafka connectivity |

**Re-processing a DLQ job**: Re-publish the original `RenderRequest` to `video.render.request.v1`.

### 2.2 Stuck/long-running jobs

**Symptom**: Worker not processing new messages; status shows same job for extended time.

**Diagnosis**:
```bash
# Check current job status
cat /tmp/maker8_status.json | jq '.current_job, .current_stage, .uptime_sec'

# Check container logs for retry sleep
docker logs --tail 100 <container> 2>&1 | grep "stage.retry_scheduled"
```

**Common causes**:
1. **Retry sleep** — Worker may be in exponential backoff (up to 6 hours).
   If the error is persistent, restart the container to skip to DLQ.
2. **FFmpeg hung** — NORMALIZE stage with no log activity.
   Restart the container.
3. **yt-dlp slow download** — Large video download in progress.
   Wait or check network.

**Forced recovery**:
```bash
docker restart <container>
```
The consumer will re-poll after restart. If the message was already committed,
it will not be re-processed (a failed result + DLQ was already emitted by the
orchestrator before retry exhaustion).

### 2.3 TTS credential issues

**Symptom**: DLQ messages with `failed_stage: TTS`, or warnings about TTS failures in results.

**Google Cloud TTS keys**:
- Keys are stored in `MAKER8_GOOGLE_TTS_KEYS_DIR` (default: `gg-tts-keys/`)
- maker8 rotates keys round-robin per video
- If all keys are exhausted or expired, TTS fails for all scenes

**Diagnosis**:
```bash
docker logs <container> 2>&1 | grep -i "tts\.\|credential"
```

**Resolution**:
1. Check if service account keys are valid: `gcloud auth activate-service-account --key-file=<key.json>`
2. Add new key files to the keys directory
3. Restart the container to pick up new keys

**ElevenLabs keys**:
- Stored in `MAKER8_ELEVENLABS_KEYS_DIR`
- Check API quota and key validity at https://elevenlabs.io/app/settings

### 2.4 Dropbox issues

**Symptom**: DLQ with `failed_stage: UPLOAD_DROPBOX`, error codes `AUTH_FAILED` or `RATE_LIMITED`.

| Error code | Retryable | Action |
|-----------|-----------|--------|
| `AUTH_FAILED` | No | Refresh token expired. Generate new one — see `DROPBOX_SETUP.md` |
| `RATE_LIMITED` | Yes | Transient; will auto-retry. If persistent, reduce throughput |
| `SERVER_ERROR` | Yes | Dropbox issue; auto-retries |
| `INVALID_CONFIG` | No | Check `MAKER8_DROPBOX_APP_KEY` / `MAKER8_DROPBOX_APP_SECRET` |

**Refresh token rotation**:
```bash
# Set new credentials in environment
MAKER8_DROPBOX_REFRESH_TOKEN=<new_token>
# Restart container
docker restart <container>
```

### 2.5 Disk space exhaustion

**Symptom**: `DISK_SPACE_LOW` errors in DOWNLOAD stage.

**Diagnosis**:
```bash
df -h /tmp/maker8
du -sh /tmp/maker8/*/
```

**Resolution**:
```bash
# Remove orphaned job directories (jobs that completed but cleanup failed)
find /tmp/maker8 -maxdepth 1 -type d -mtime +1 -exec rm -rf {} +
```

The pipeline requires ≥500MB free space before each DOWNLOAD stage.

---

## 3. Credential Rotation Schedule

| Credential | Location | Rotation |
|-----------|----------|----------|
| Kafka SASL | `MAKER8_KAFKA_USERNAME` / `MAKER8_KAFKA_PASSWORD` | Per infrastructure policy |
| Dropbox OAuth | `MAKER8_DROPBOX_REFRESH_TOKEN` | When token stops working (long-lived) |
| Google Cloud TTS | `gg-tts-keys/*.json` | When keys are revoked or quotas change |
| ElevenLabs | `elevenlabs-keys/` or `MAKER8_ELEVENLABS_API_KEY` | When API key expires or quota exhausted |

After rotating any credential, restart the container for it to take effect.

---

## 4. Monitoring

### Key log events to alert on

| Log event | Severity | Meaning |
|-----------|----------|---------|
| `job.failure_summary` | ERROR | Job failed permanently |
| `upload.auth_error` | ERROR | Dropbox auth broken |
| `orchestrator.invalid_payload` | ERROR | Unparseable Kafka message |
| `stage.retry_exhausted` | ERROR | All retries used up |
| `stage.retry_scheduled` | WARN | Stage retry in progress |
| `tts.all_providers_failed` | ERROR | No TTS provider works |

### Prometheus metrics (if enabled)

| Metric | Labels | Description |
|--------|--------|-------------|
| `maker8_jobs_succeeded_total` | — | Successful jobs |
| `maker8_jobs_failed_total` | stage, error_code | Failed jobs by stage |
| `maker8_dlq_emitted_total` | — | DLQ messages |
| `maker8_stage_duration_seconds` | stage, status | Per-stage timing |
| `maker8_retries_scheduled_total` | stage | Retry count |
| `maker8_dependency_failures_total` | dependency | External service errors |

---

## 5. Deployment Checklist

Before deploying a new version:

1. [ ] Run `python -m pytest tests/` — all tests pass
2. [ ] Verify golden fixture tests pass (`test_contracts.py`)
3. [ ] Check that `render_contracts/` is in sync with editor8 (`diff` both copies)
4. [ ] Build Docker image: `docker build -t maker8:latest .`
5. [ ] Verify health probes work: liveness + readiness files created on startup
6. [ ] Smoke test: send a minimal `RenderRequest` and verify `RenderResult` is emitted

---

## 6. Rollback Procedure

1. Stop the current container: `docker stop <container>`
2. Deploy the previous image tag: `docker run ... maker8:<previous_tag>`
3. Verify consumer reconnects and processes messages
4. Check logs for errors

Kafka messages are retained by broker retention policy. Un-committed messages
will be re-delivered to the new container.
