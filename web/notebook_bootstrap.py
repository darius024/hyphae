"""
Notebook bootstrap to make web modules importable everywhere.

Usage (at the top of a notebook):

```python
from web.notebook_bootstrap import bootstrap
bootstrap()  # adds repo paths and sane env defaults
from web import app, db, embed
```
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def bootstrap() -> None:
    """Ensure repository paths and safe defaults are in place for notebooks.

    - Adds repo root, hyphae/, and web/ to sys.path if missing
    - Sets USE_DUMMY_EMBED=1 by default to avoid huggingface downloads in notebooks
    - Sets TRANSFORMERS_OFFLINE/HF_HUB_OFFLINE to keep imports offline-safe
    """
    repo_root = Path(__file__).resolve().parents[2]  # .../Projects/Hyphae
    project_root = repo_root / "hyphae"
    web_dir = project_root / "web"

    for p in (repo_root, project_root, web_dir, project_root / "src", repo_root / "cactus" / "python" / "src"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    os.environ.setdefault("USE_DUMMY_EMBED", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

__all__ = ["bootstrap"]
