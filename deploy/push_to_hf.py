"""Upload the app to a Hugging Face Docker Space.

Usage (after `hf auth login`):
    python deploy/push_to_hf.py <user>/<space-name>

Stages app/, policy_texts/, requirements.txt and agentic_hr.db, plus
deploy/Dockerfile, deploy/start.sh and deploy/SPACE_README.md (as README.md)
at the Space root. Secrets are set in the Space settings, never uploaded.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.db.bak*", "*.db-journal")


def stage(dest: Path) -> None:
    shutil.copytree(ROOT / "app", dest / "app", ignore=IGNORE)
    shutil.copytree(ROOT / "policy_texts", dest / "policy_texts", ignore=IGNORE)
    shutil.copy2(ROOT / "requirements.txt", dest / "requirements.txt")
    shutil.copy2(ROOT / "agentic_hr.db", dest / "agentic_hr.db")
    shutil.copy2(DEPLOY / "Dockerfile", dest / "Dockerfile")
    shutil.copy2(DEPLOY / "SPACE_README.md", dest / "README.md")
    # A CRLF shebang line breaks the container start on Linux.
    start = (DEPLOY / "start.sh").read_bytes().replace(b"\r\n", b"\n")
    (dest / "start.sh").write_bytes(start)


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        sys.exit("usage: python deploy/push_to_hf.py <user>/<space-name>")
    repo_id = sys.argv[1]
    api = HfApi()
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(
            repo_id=repo_id,
            repo_type="space",
            folder_path=tmp,
            commit_message="Deploy Yusor demo",
            delete_patterns=["app/**", "policy_texts/**"],
        )
    print(f"Pushed. Set secrets at https://huggingface.co/spaces/{repo_id}/settings")


if __name__ == "__main__":
    main()
