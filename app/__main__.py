"""`python -m app` - production entry point (Railway, Docker, anywhere).

Reads the port from $PORT (set by Railway/Render/Heroku-style hosts) so the
start command needs no shell variable expansion.
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,          # trust X-Forwarded-* from the platform's proxy
        forwarded_allow_ips="*",
    )
