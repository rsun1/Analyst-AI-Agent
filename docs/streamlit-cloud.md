# Deploying the UI on Streamlit Community Cloud

The UI is a thin client: it only calls the HTTP API (`api_client.py`), so it needs no database or OpenAI key.

1. Push `main` to GitHub (the repo must be public, or grant Streamlit access).
2. At share.streamlit.io choose **Create app** > the repo, branch `main`, main file `AI Agent/app.py`.
3. Advanced settings > **Secrets**: paste (see `AI Agent/.streamlit/secrets.toml.example`):
   ```toml
   API_URL = "https://<service>.ai-builders.space"
   API_KEY = "<same value as the API service's API_KEY>"
   ```
4. Deploy. Dependencies come from the repo-root `requirements.txt` (UI-only). It must not live in `AI Agent/`: Streamlit Cloud looks next to the app first, and its installer fails on the space in that folder name.

Notes
- Secrets live only in Streamlit's settings, never in git. Anyone with the UI URL can use the API through it, so keep the app private or share it only with people you trust: the key is held server-side and not shown to visitors.
- The API (Koyeb, free tier) may cold-start; the first request after idle time can be slow.
- `/analyze` can take tens of seconds; the client already allows 300 s.
