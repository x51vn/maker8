# Dropbox Setup Guide

## Problem

The current Dropbox refresh token in `.env` is **invalid** (`invalid_access_token`).

This means:
- ✋ Token is revoked, expired, or doesn't match the app credentials
- ✋ Upload stage will fail during renders
- ✋ But the app can still start and run other stages

## Solution: Generate New Refresh Token

### Option 1: Using the Helper Script (Recommended)

```bash
# Run the token generator script
python3 scripts/generate_dropbox_token.py

# Follow prompts to:
# 1. Enter your App Key and App Secret
# 2. Authorize in browser
# 3. Get authorization code
# 4. Get refresh token
```

### Option 2: Manual Setup via Dropbox Console

1. **Go to Dropbox App Console:** https://www.dropbox.com/developers/apps

2. **Find or Create Your App**
   - If you don't have an app, create one:
     - Choose "Scoped access"
     - Choose "Full Dropbox" (or "App folder")
     - Name it something like "Maker8"

3. **Get App Credentials**
   - Settings tab → App key & secret
   - Copy both values

4. **Generate Access Token**
   - Settings tab → OAuth 2
   - Click "Generate" in the "Generated access token" section
   - This gives you a **short-lived** access token (expires in hours)

5. **Get Refresh Token via Manual OAuth Flow**
   - Visit this URL (replace `APP_KEY`):
   ```
   https://www.dropbox.com/oauth2/authorize?client_id=APP_KEY&response_type=code&token_access_type=offline&redirect_uri=http://localhost:8080
   ```
   - Authorize the app
   - You'll be redirected to `http://localhost:8080?code=...`
   - Copy the `code` value

6. **Exchange Code for Refresh Token**
   ```bash
   curl -X POST https://api.dropboxapi.com/oauth2/token \
     -d "code=AUTHORIZATION_CODE" \
     -d "grant_type=authorization_code" \
     -d "client_id=APP_KEY" \
     -d "client_secret=APP_SECRET"
   ```
   - Response will include `refresh_token`

### Step 3: Update .env

Add the refresh token to `.env`:

```bash
# In .env file, update the existing line:
MAKER8_DROPBOX_REFRESH_TOKEN=<your_new_refresh_token>
```

Or set as environment variable:

```bash
export MAKER8_DROPBOX_REFRESH_TOKEN='<your_new_refresh_token>'
```

### Step 4: Verify

Run the app:

```bash
python3 src/maker8/app.py
```

Check the logs:

```
{"event": "dropbox.auth_validated", "account_id": "...", "email": "..."}
```

If you see this, credentials are valid! ✅

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `invalid_access_token` | Token is revoked or expired | Generate new token |
| `invalid_client_id` | App key is wrong | Check app key in console |
| `invalid_grant` | Code is expired or used | Generate new code |
| `unauthorized` | Request is not authenticated | Check app secret is correct |

## App Permissions

Make sure your Dropbox app has these scopes enabled:

- ✅ `files.content.write` - Write file content
- ✅ `files.metadata.write` - Write file metadata

Check in: Dropbox App Console → Permissions tab

## Refresh Token Lifetime

- Refresh tokens **don't expire** (unless revoked)
- Your app will automatically refresh the access token as needed
- The refresh token in `.env` should be permanent

## Testing Without Dropbox

If you want to test rendering without Dropbox:

Edit `src/maker8/pipeline/upload.py`:

```python
def execute(self, ctx: PipelineContext) -> None:
    log.warning("upload.skipped_for_testing")
    # Skip upload for now
    return

    # ... rest of code ...
```

This lets you test the render pipeline without upload.

## More Help

- Dropbox API Docs: https://www.dropbox.com/developers/documentation
- OAuth 2: https://www.dropbox.com/developers/documentation/http/guides/file-upload
