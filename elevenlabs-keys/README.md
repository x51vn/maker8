# ElevenLabs TTS – API Keys

Place ElevenLabs **API key files** in this directory.

Each file must contain **exactly one API key** (plain text, no quotes).
Recognised extensions: `.txt`, `.key`.

The Maker8 render worker loads all key files at startup and rotates
through them in alphabetical order (round-robin, one key per video).

## Quick setup

1. Go to **ElevenLabs → Profile → API Keys** and generate key(s).
2. Save each key to a separate file:

   ```
   elevenlabs-keys/
   ├── account-a.txt
   ├── account-b.txt
   └── account-c.key
   ```

   File content example (`account-a.txt`):
   ```
   sk_abc123def456ghi789
   ```

3. Start the worker – it logs how many keys were loaded:
   ```
   tts_service.ready  google_keys=15  elevenlabs_keys=3
   ```

## Notes

* If this directory is empty or missing, the provider falls back to the
  single key in `MAKER8_ELEVENLABS_API_KEY`.
* **Never commit real keys to git.** The `.gitignore` excludes `*.txt`
  and `*.key` in this folder.
