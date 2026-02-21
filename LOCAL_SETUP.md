# Local setup (macOS, zsh)

This file summarizes the minimal commands to prepare this repository on a Mac (zsh).

1) Run the automated setup script (recommended):

```zsh
cd /path/to/hyphae
./setup.sh
```

The script will:
- clone the `cactus` repo into `./cactus` if missing
- create a Python virtualenv in `.venv` and install `requirements.txt`
- attempt to source `cactus/setup` and run `cactus build --python` and model download (these steps require network and may be large)

2) Authentication & keys

- Get your Cactus key from: https://cactuscompute.com/dashboard/api-keys
- In a new terminal (per README) run:

```zsh
cd cactus
source ./setup
cactus auth
```

- Install `google-genai` (already done by `setup.sh` into `.venv`) and obtain a Gemini API key from Google AI Studio.

Export it in your shell (example):

```zsh
export GEMINI_API_KEY="your-key-here"
```

3) Run the benchmark (once model weights and keys are available):

```zsh
source .venv/bin/activate
python benchmark.py
```

Notes
- The `cactus` build and model download steps are heavy and may take significant time and disk.
- If you only want to exercise cloud-only paths, you can skip the `cactus` clone/build steps and ensure `GEMINI_API_KEY` is set; the code will attempt cloud calls.
- Do not commit your API keys to source control.

Secrets & API keys (important)

- Never paste your API keys into public channels or commit them to the repository. If you accidentally exposed a key (for example by pasting it into a public chat), revoke and re-generate it immediately from the provider's dashboard.
- Use a local `.env` file (not committed) or use macOS Keychain / direnv to manage keys. A `.env.example` file is included in the repo to show the expected variable names.

Example to create a private `.env` and load it into your shell:

```zsh
cp .env.example .env
# Edit .env and add your real keys (do NOT commit .env)
source .env
```

Or add the key permanently to your zsh config (only if your machine is secure):

```zsh
echo 'export GEMINI_API_KEY="your-gemini-key"' >> ~/.zshrc
source ~/.zshrc
```

If you have already pasted keys into a public place, rotate/revoke them now.
