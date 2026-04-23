---
name: image-gen
description: Generate images via OpenAI gpt-image-* or OpenRouter gpt-5.4-image-2 using the `imagegen` CLI. Auto-saves raw API response to avoid paid re-runs when extraction fails. Use when user asks to generate/create an image via API.
version: 1.0.0
---

# Image Generation

Use the `imagegen` CLI (already on PATH, symlinked from `~/.claude/skills/image-gen/image_gen.py`).

## Usage

```bash
# Default: OpenRouter / openai/gpt-5.4-image-2 (no org verification needed)
imagegen "your prompt here"

# Use OpenAI direct (cheaper, but gpt-image-2 needs org verification)
imagegen "your prompt" --provider openai

# OpenAI fallback model (no verification needed)
imagegen "your prompt" --provider openai --model gpt-image-1

# Re-extract from cached response (FREE — no API call)
imagegen

# Force a new API call even if cache exists
imagegen "new prompt" --force

# Custom output dir / size / quality
imagegen "your prompt" --out /tmp/myimages --size 1024x1024 --quality medium
```

## What It Does

1. Calls the image generation API
2. Saves raw JSON response to `image_response.json` BEFORE parsing — so failed extraction doesn't lose paid base64 data
3. Extracts and writes PNG file(s) to the output directory
4. Logs token usage and total cost

## Provider Selection Cheat-Sheet

- `--provider openai` (default) + `--model gpt-image-2` → cheapest+best, **needs org verification**
- `--provider openai` + `--model gpt-image-1` → fallback when org not verified (~$0.03)
- `--provider openrouter` → uses `openai/gpt-5.4-image-2`, no verification needed (~$0.23)

## Requirements

- `OPENAI_API_KEY` env var (for openai provider)
- `OPENROUTER_API_KEY` env var (for openrouter provider)
- Python 3 with `httpx` installed

If `httpx` is missing in the project venv: activate venv, then `uv pip install httpx`.
