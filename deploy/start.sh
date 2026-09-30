#!/bin/sh
set -e

# Admin password comes from the ADMIN_PASSWORD Space secret so its hash is
# never published with the database file.
if [ -n "$ADMIN_PASSWORD" ]; then
    python -c "
import os, sqlite3
from app.config import get_settings
from app.security.passwords import hash_password
con = sqlite3.connect(get_settings().sqlite_path)
con.execute('UPDATE users SET password_hash = ? WHERE username = ?',
            (hash_password(os.environ['ADMIN_PASSWORD']), 'EMP-0048'))
con.commit()
"
fi

uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &

exec streamlit run app/ui/streamlit_app.py \
    --server.port 7860 --server.address 0.0.0.0 \
    --server.headless true --browser.gatherUsageStats false
