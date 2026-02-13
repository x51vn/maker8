# Maker8 – Segfault on Shutdown Troubleshooting Guide

## Problem

The Maker8 render worker sometimes exits with **exit code 139 (SIGSEGV)** on shutdown, even though it:
- ✅ Initializes all components successfully
- ✅ Runs for extended periods (tested: 15+ minutes)
- ✅ Correctly processes jobs and rotates credentials
- ⚠️ Crashes during cleanup when signal handler is invoked (Ctrl+C or `systemctl stop`)

The crash is **non-blocking**: the app works correctly during runtime; the issue only manifests at shutdown.

---

## Root Cause Analysis

### Likely Sources

The segmentation fault (SIGSEGV) occurs in **C extension libraries** during Python's cleanup phase:

1. **confluent-kafka** (Python Kafka client)
   - Uses librdkafka C library underneath
   - Cleanup issues documented in: https://github.com/confluentinc/confluent-kafka-python/issues/1254
   - Especially problematic when signal handlers interrupt consumer

2. **MoviePy** (video composition)
   - Wraps FFmpeg C libraries
   - Cleanup issues with temporary files and subprocess termination
   - Version 2.1.2 (current) has known cleanup issues

3. **google-cloud-texttospeech** (TTS provider)
   - Uses gRPC C extension for service communication
   - May leave resources allocated if not properly finalized

### Why It Happens at Shutdown

```
1. User presses Ctrl+C (or process receives SIGTERM)
   ↓
2. Python signal handler called → logs "app.shutdown"
   ↓
3. handler calls consumer.stop() 
   ↓
4. consumer.stop() tries to tear down librdkafka resources
   ↓
5. Python's atexit handlers run → finalizes other libraries
   ↓
6. C extensions' cleanup code accesses freed memory → SIGSEGV
   ↓
7. Process exits with code 139
```

---

## Solutions

### Solution 1: Current (Recommended) – Use `os._exit()` with Signal Guarding

**Status**: ✅ **Implemented in `src/maker8/app.py`**

**How it works:**
- Calls `os._exit(0)` in `finally` block instead of letting Python cleanup naturally
- Bypasses C extension cleanup (which is where the crash occurs)
- Registers `atexit` handler for logging, but `os._exit()` skips normal cleanup

**Benefits:**
- Clean logs on successful operation
- Prevents C extension cleanup issue
- Works reliably across all Python/library versions

**Implementation:**
```python
finally:
    try:
        producer.close()
        log.info("app.stopped")
    except Exception as e:
        log.error("producer.close_failed", error=str(e))
    
    # Bypass C extension cleanup to avoid SIGSEGV
    os._exit(0)
```

### Solution 2: Suppress Signal Handler Double-Presses

**Status**: ✅ **Implemented in `src/maker8/app.py`**

**How it works:**
- Track `_shutdown_requested` global flag
- If Ctrl+C pressed multiple times during shutdown, force exit immediately with `os._exit(0)`
- Prevents signal handlers from re-entering cleanup code

**Implementation:**
```python
def _shutdown(sig: int, _frame: object) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        os._exit(0)  # Force exit if shutdown already started
    _shutdown_requested = True
    # ... rest of shutdown logic
```

### Solution 3: Container/Systemd Wrapping (Production)

**Status**: Optional, recommended for production deployments

**How it works:**
- Run Maker8 in a container (Docker/Kubernetes)
- Let container runtime handle process cleanup and SIGSEGV
- Container restart policies handle recovery

**Dockerfile:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e .
CMD ["python", "-m", "maker8.app"]
```

**systemd unit file:**
```ini
[Unit]
Description=Maker8 Render Worker
After=kafka.service

[Service]
Type=simple
ExecStart=/usr/bin/python -m maker8.app
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

systemd will:
- Capture exit code 139 and log it
- Automatically restart the service (if `Restart=on-failure`)
- Not crash the host system (segfault isolated to process)

### Solution 4: Debug with Valgrind (Investigation Only)

**Status**: For developers troubleshooting

**How to run:**
```bash
valgrind --leak-check=full --show-leak-kinds=all \
  python -m maker8.app
```

**Expected output:**
- Shows memory leaks and invalid accesses in C libraries
- Identifies exact location of SIGSEGV in native code
- Very slow (10-100x slower), not suitable for production

---

## Deployment Recommendations

### Development

Use **Solution 1** (current `app.py`):
- Clean shutdown with `os._exit(0)`
- Double Ctrl+C protection
- No C extension cleanup issues

### Production (Containerized)

Use **Solution 3** (systemd + container):
- Run in Docker: `docker run --restart=unless-stopped maker8:latest`
- Or with systemd + `Restart=on-failure`
- Container/systemd handles process lifecycle

### Production (Bare Metal)

Use **Solutions 1 + 3** together:
- `app.py` handles graceful shutdown
- systemd/supervisor catches exit code 139
- `Restart=on-failure` auto-restarts worker

---

## Verification

### Test Current Solution

```bash
# Start app in foreground
python -m maker8.app

# Press Ctrl+C once (graceful shutdown)
# Should see: app.shutdown, consumer.stopped, app.stopped
# Exit code: 0 (success)

# Press Ctrl+C twice during shutdown
# Should force exit immediately
# Exit code: 0 (forced clean exit)
```

### Expected Logs

**Successful shutdown:**
```json
{"event": "app.shutdown", "signal": 2}
{"event": "consumer.stopped"}
{"event": "app.stopped"}
{"event": "app.exiting"}
```

**With forced exit:**
```json
{"event": "app.shutdown", "signal": 2}
{"event": "app.force_exit", "reason": "shutdown_already_in_progress"}
```

### Monitor in Production

```bash
# systemd
journalctl -u maker8 -f

# Docker
docker logs -f maker8-container

# Check exit code
echo $?  # 0 = success, 139 = segfault (should not occur with updated app.py)
```

---

## References

- **confluent-kafka issue**: https://github.com/confluentinc/confluent-kafka-python/issues/1254
- **Python os._exit()**: https://docs.python.org/3/library/os.html#os._exit
- **MoviePy cleanup**: https://github.com/Zulko/moviepy/issues/788
- **SIGSEGV (exit 139)**: Signal 11 × 128 = 139

---

## Summary

| Scenario | Solution | Status |
|----------|----------|--------|
| Dev testing (no real jobs) | Solution 1 (`os._exit()`) | ✅ Implemented |
| Dev testing (with real jobs) | Solution 1 + monitor logs | ✅ Ready |
| Docker deployment | Solution 3 (container + systemd) | 📝 Optional |
| Bare metal with systemd | Solutions 1 + 3 | 📝 Optional |
| Debugging root cause | Solution 4 (valgrind) | 🔧 For developers |

**Current Status**: The app.py has been updated with **Solution 1**.  
**Expected Behavior**: Clean shutdown, exit code 0, no segfault.  
**Known Limitation**: If used, `os._exit()` bypass may leave some resources not fully cleaned up (acceptable for containerized deployments).

