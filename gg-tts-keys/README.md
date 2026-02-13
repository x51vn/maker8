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
   tts_service.ready  google_keys=15  elevenlabs_keys=0
   ```

## Current accounts

| Email | Key file |
|---|---|
| myx51fc@gmail.com | google_x51newsprojects_text_to_speech.json |
| myx51labs@gmail.com | science52-219c007d0599.json |
| vubakninh@gmail.com | duvu19-43d0ab45b7a4.json |
| hoaivubk@gmail.com | smarttrace-1250-65ac07859427.json |
| vud.esoft@gmail.com | vanhoalichsu53-37e892d4f0d7.json |
| vudq.vn@gmail.com | myenglishapp-1597398018005-25eb5be4e0ed.json |

## Notes

* Files are sorted by name → deterministic ordering across restarts.
* If this directory is empty or missing, the provider falls back to
  Application Default Credentials (ADC) or the single path in
  `MAKER8_GOOGLE_APPLICATION_CREDENTIALS`.
* **Never commit real credentials to git.** The `.gitignore` excludes
  `*.json` in this folder.
