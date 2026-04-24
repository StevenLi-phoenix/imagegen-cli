---
name: image-gen
description: Generate images via OpenAI, OpenRouter, or Google Gemini through the `imagegen` CLI. Auto-routes by model name and auto-saves raw API response to avoid paid re-runs when extraction fails. Use when user asks to generate/create an image via API.
version: 1.1.0
---

# Image Generation

Use the `imagegen` CLI (install per the [repo README](https://github.com/StevenLi-phoenix/imagegen-cli) — symlinks `image_gen.py` into `~/.local/bin/imagegen`).

## Usage

```bash
# Default: OpenRouter / openai/gpt-5.4-image-2 (no org verification needed)
imagegen "your prompt here"

# Auto-routes by model name — no --provider needed
imagegen "prompt" --model gpt-image-2                  # → OpenAI direct
imagegen "prompt" --model gemini-3-pro-image-preview   # → Gemini direct
imagegen "prompt" --model imagen-4.0-generate-preview  # → Gemini Imagen :predict
imagegen "prompt" --model openai/gpt-5.4-image-2       # → OpenRouter

# Force a specific provider when the model name is ambiguous
imagegen "prompt" --provider openai --model gpt-image-1

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

- `openrouter` (default) → `openai/gpt-5.4-image-2`, no org verification, ~$0.22/img
- `openai` + `gpt-image-2` → cheapest OpenAI direct, **needs org verification**, ~$0.05
- `openai` + `gpt-image-1` → fallback when org not verified, ~$0.03
- `gemini` + `gemini-3-pro-image-preview` → Google direct, see [pricing](https://ai.google.dev/pricing)

Auto-detection: model with `/` → OpenRouter; starts with `gemini`/`imagen` → Gemini; starts with `dall-e`/`gpt-image` → OpenAI.

## Requirements

- `OPENAI_API_KEY` env var (for openai provider)
- `OPENROUTER_API_KEY` env var (for openrouter provider)
- `GEMINI_API_KEY` env var (for gemini provider, from Google AI Studio)
- Python 3 with `httpx` installed

If `httpx` is missing in the project venv: activate venv, then `uv pip install httpx`.
