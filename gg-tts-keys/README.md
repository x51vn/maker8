# Google Cloud TTS – Service Account Keys

Place Google Cloud **service account JSON** files in this directory.

The Maker8 render worker loads **all** `*.json` files at startup and
rotates through them in alphabetical order (round-robin, one key per
video).

## Quick setup

1. Go to **Google Cloud Console → IAM & Admin → Service Accounts**.
2. Create (or reuse) a service account with the role
   **Cloud Text-to-Speech API User**.
3. Generate a JSON key and download it.
4. Copy the file here.
5. Start the worker – it logs how many keys were loaded:
   ```
   tts_service.ready  google_keys=1  elevenlabs_keys=0
   ```

## Notes

* Files are sorted by name → deterministic ordering across restarts.
* If this directory is empty or missing, the provider falls back to
  Application Default Credentials (ADC) or the single path in
  `MAKER8_GOOGLE_APPLICATION_CREDENTIALS`.
* **Never commit real credentials to git.** The `.gitignore` excludes
  `*.json` in this folder.
