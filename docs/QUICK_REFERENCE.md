# Maker8 – Quick Reference Guide

## ⚡ Quick Start

### 1. Setup Credentials

```bash
# Google Cloud service account keys (15 accounts)
cp ~/my-gcp-keys/*.json ./gg-tts-keys/

# ElevenLabs API keys (optional - falls back to gTTS if empty)
cp ~/my-elevenlabs-keys/*.txt ./elevenlabs-keys/

# Verify
ls -la gg-tts-keys/         # Should see *.json files
ls -la elevenlabs-keys/     # Should see *.txt or *.key files
```

### 2. Configure Environment

```bash
# Copy template
cp .env.example .env

# Edit .env with your settings
nano .env
```

**Required fields**:
```env
# Kafka
MAKER8_KAFKA_BOOTSTRAP_SERVERS=<kafka-host>:9094
MAKER8_KAFKA_SASL_MECHANISM=PLAIN
MAKER8_KAFKA_USERNAME=render
MAKER8_KAFKA_PASSWORD=...

# Dropbox OAuth2
MAKER8_DROPBOX_APP_KEY=...
MAKER8_DROPBOX_APP_SECRET=...
MAKER8_DROPBOX_REFRESH_TOKEN=...

# TTS Providers
MAKER8_GOOGLE_TTS_KEYS_DIR=gg-tts-keys
MAKER8_ELEVENLABS_KEYS_DIR=elevenlabs-keys
MAKER8_ELEVENLABS_API_KEY=...  # Optional if using key directory
```

### 3. Run

```bash
# Development (foreground)
python -m maker8.app

# Docker (recommended for production)
docker run --restart=unless-stopped \
  --env-file .env \
  -v $(pwd)/gg-tts-keys:/app/gg-tts-keys \
  -v $(pwd)/elevenlabs-keys:/app/elevenlabs-keys \
  maker8:latest
```

---

## 📊 How Credential Rotation Works

```
┌─ Render Job Arrives ─────────────────────────┐
│                                             │
│  1. TTSStage.execute() called              │
│  2. Get next Google credentials            │
│     └─ Round-robin through all 15 keys     │
│  3. Get next ElevenLabs key                │
│     └─ Round-robin through all keys        │
│  4. For each scene in video:               │
│     └─ Use SAME credentials (not rotating) │
│  5. Move to next video                     │
│     └─ Credentials advance again           │
└─────────────────────────────────────────────┘

Example: 3 videos, 2 Google accounts
Video 1 → account[0]
Video 2 → account[1]
Video 3 → account[0]  (wraps around)
```

---

## 🔑 Credential File Formats

### Google Cloud Service Accounts

**File**: `gg-tts-keys/account-name.json`  
**Content**: JSON service account key
```json
{
  "type": "service_account",
  "project_id": "my-project",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",
  "client_email": "sa@my-project.iam.gserviceaccount.com",
  ...
}
```

**Quota per account**: Usually 1M requests/month (check your GCP billing)

### ElevenLabs API Keys

**File**: `elevenlabs-keys/api-key-1.txt` (one filename = one API key)  
**Content**: Plain text API key
```
sk-your-eleven-labs-api-key-here
```

**Quota per key**: Check your ElevenLabs account dashboard

**Fallback**: If no files in `/elevenlabs-keys/`, uses single key from `MAKER8_ELEVENLABS_API_KEY` env var

---

## 📝 Monitoring

### Logs

**JSON structured logs** are written to:
- Stdout (default)
- To redirect: `python -m maker8.app 2>&1 | tee app.log`

**Key log events**:
```json
{"event": "app.starting", "version": "0.1.0"}
{"event": "key_ring.loaded_json", "count": 15, "directory": "gg-tts-keys"}
{"event": "tts_service.ready", "google_keys": 15, "elevenlabs_keys": 0}
{"event": "consumer.started", "topic": "video.render.request.v1"}
{"event": "tts_service.next_google_credentials", "ordinal": 1}  // Video 1
{"event": "render.completed", "video_id": "...", "status": "success"}
```

**Search for credential rotation**:
```bash
grep -i "next_google\|next_elevenlabs" app.log
```

### Healthcheck

```bash
# Check if consumer is listening
ps aux | grep "python -m maker8.app"

# Check credentials loaded
curl http://localhost:8080/health 2>/dev/null || echo "no health endpoint"

# Check logs for errors
tail -f app.log | grep -i "error\|failed"
```

---

## 🐛 Troubleshooting

### Segfault on Shutdown (Exit Code 139)

**Expected**: App exits cleanly  
**Actual**: `Segmentation fault (core dumped)`

**Solution**: Already fixed in app.py with `os._exit()`  
**Read**: [docs/SEGFAULT_TROUBLESHOOTING.md](docs/SEGFAULT_TROUBLESHOOTING.md)

### No Credentials Loaded

```json
{"event": "key_ring.loaded_json", "count": 0, "directory": "gg-tts-keys"}
```

**Check**:
```bash
ls -la gg-tts-keys/              # Are files there?
file gg-tts-keys/*.json          # Are they valid JSON?
```

### Wrong Credentials Format

```json
{"event": "error", "message": "Failed to parse credentials", ...}
```

**Check**:
```bash
# Google Cloud: Must be valid service account JSON
python -c "import json; json.load(open('gg-tts-keys/your-key.json'))"

# ElevenLabs: Must be plain text, one key per file
cat elevenlabs-keys/key1.txt
```

### Kafka Connection Failed

```json
{"event": "kafka.error", "code": "SASL_AUTH_FAILED"}
```

**Check** `.env`:
```bash
grep KAFKA .env | grep -v "^#"
```

**Verify credentials**:
```bash
# Test Kafka broker connection
python -c "
from confluent_kafka import KafkaConsumer
c = KafkaConsumer(
    bootstrap_servers='<kafka-host>:9094',
    security_protocol='SASL_PLAINTEXT',
    sasl_mechanism='PLAIN',
    sasl_plain_username='render',
    sasl_plain_password='...',
)
print('Connected!')
"
```

### Dropbox Upload Failed

```json
{"event": "upload.error", "message": "Invalid refresh token"}
```

**Check** `.env`:
```bash
grep DROPBOX .env | grep -v "^#"
```

**Renew token**:
1. Go to https://www.dropbox.com/developers/apps
2. Generate new refresh token
3. Update `.env`
4. Restart app

---

## 🚀 Performance Tips

### Optimize for Speed

```env
# Reduce logging overhead
MAKER8_LOG_LEVEL=warning    # Instead of info

# Use lightweight TTS
MAKER8_DEFAULT_TTS_PROVIDER=gtts  # Fastest, no auth needed
```

### Optimize for Quality

```env
# Use premium TTS providers
MAKER8_DEFAULT_TTS_PROVIDER=google  # Higher quality
# Or
MAKER8_DEFAULT_TTS_PROVIDER=elevenlabs  # More voices
```

### Optimize for Cost

```env
# Reduce API calls
MAKER8_DEFAULT_TTS_PROVIDER=gtts  # Free

# Or rotate through expensive providers less frequently
# (edit TTSStage to skip rotation for low-priority scenes)
```

---

## 🔄 Scaling Horizontally

**Current design**: 1 app instance = 1 job at a time (simple pipeline)

**To scale**: Run multiple workers pointing to same Kafka topic
```bash
# Terminal 1
docker run --name maker8-1 --restart=unless-stopped maker8:latest

# Terminal 2
docker run --name maker8-2 --restart=unless-stopped maker8:latest

# Terminal 3 (optional)
docker run --name maker8-3 --restart=unless-stopped maker8:latest
```

Each worker:
- Consumes from `video.render.request.v1` independently
- Rotates credentials from same key directories
- Uploads to same Dropbox folder
- Emits results to `video.render.result.v1`

**Warning**: Credential rotation is thread-safe but **not atomic**. With multiple workers, you may see some keys used twice before others are used once (acceptable for quota fairness).

---

## 📦 Deployment Checklist

- [ ] Credentials placed in `gg-tts-keys/` and `elevenlabs-keys/`
- [ ] `.env` file created with all required fields
- [ ] Kafka broker reachable and SASL credentials valid
- [ ] Dropbox OAuth2 token generated and valid
- [ ] Docker image built (if using container)
- [ ] App tested locally: `python -m maker8.app` then Ctrl+C
- [ ] App runs in production (container or systemd)
- [ ] Logs monitored for errors
- [ ] At least 1 test render job sent to verify round-robin rotation

---

## 🔗 Documentation

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | Project overview |
| [docs/IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Full implementation report |
| [docs/TTS_CREDENTIAL_ROTATION.md](TTS_CREDENTIAL_ROTATION.md) | Architecture deep-dive |
| [docs/SEGFAULT_TROUBLESHOOTING.md](SEGFAULT_TROUBLESHOOTING.md) | Root cause analysis |
| [gg-tts-keys/README.md](../gg-tts-keys/README.md) | Google Cloud setup |
| [elevenlabs-keys/README.md](../elevenlabs-keys/README.md) | ElevenLabs setup |

---

## ❓ FAQ

**Q: Can I use only one provider?**  
A: Yes. Set `MAKER8_DEFAULT_TTS_PROVIDER=gtts` (free) and leave other key directories empty.

**Q: What if I run out of quota?**  
A: Add more credentials to the key directory. The round-robin will distribute load. No code changes needed.

**Q: Does the round-robin track failed requests?**  
A: No, it's simple index-based rotation. If a key hits quota, the job fails and retries with the next key (per retry policy).

**Q: Can I update credentials without restarting?**  
A: No, credentials are loaded at startup. Add files to directory, then restart the app to pick them up.

**Q: What if an ElevenLabs key expires?**  
A: Remove the `.txt` file from `elevenlabs-keys/`, or it will keep failing until rotated to next key. Consider auto-removing expired keys.

**Q: How do I test credential rotation locally?**  
A: See [docs/TTS_CREDENTIAL_ROTATION.md](TTS_CREDENTIAL_ROTATION.md) for integration test example.

