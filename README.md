# imagegen

A tiny CLI for generating images via OpenAI's `gpt-image-*` family or OpenRouter's `openai/gpt-5.4-image-2`, with **mandatory raw-response caching** so failed extraction never costs a second API call.

## Why

Image generation APIs cost real money per call. If your script's parsing logic is wrong, or the response format changes, the base64 image data is lost — and you pay again to retry. `imagegen` always saves the raw API response to disk *before* parsing, so:

- **Re-running the script with no changes** extracts from cache (free).
- **Fixing a parser bug** extracts from cache (free).
- **Inspecting the raw token usage** never requires a re-call.

## Install

```bash
git clone https://github.com/StevenLi-phoenix/imagegen.git
cd imagegen
pip install httpx
chmod +x image_gen.py

# Optional: put it on your PATH
ln -s "$PWD/image_gen.py" ~/.local/bin/imagegen
```

## Setup

Set one (or both) of:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...    # default provider
export OPENAI_API_KEY=sk-...               # for --provider openai
```

Get an OpenRouter key at https://openrouter.ai/keys (no org verification required).

## Usage

```bash
# Default: OpenRouter / openai/gpt-5.4-image-2 (no org verification needed)
imagegen "a red panda holding a sign that says hello"

# Re-extract from cached response (FREE — no API call)
imagegen

# Force a new API call even if cache exists
imagegen "new prompt" --force

# Use OpenAI direct (cheaper, but gpt-image-2 needs org verification)
imagegen "your prompt" --provider openai

# OpenAI fallback model (no verification needed)
imagegen "your prompt" --provider openai --model gpt-image-1

# Custom output dir / size / quality
imagegen "your prompt" --out /tmp/myimages --size 1024x1024 --quality medium
```

## Output

Each call writes to the output directory (default: cwd):

- `image_response.json` — full raw API response (used as the cache)
- `image_0.png` (OpenAI) or `image_or_0.png` (OpenRouter) — the generated PNG

And logs to stderr:

```
2026-04-23 12:54:50 Calling openrouter (model=openai/gpt-5.4-image-2)...
2026-04-23 12:57:16 HTTP 200 in 145.7s
2026-04-23 12:57:16 Raw response → image_response.json (377789 bytes)
2026-04-23 12:57:16 Saved image_or_0.png (282385 bytes)
2026-04-23 12:57:16 --- Usage & Cost ---
2026-04-23 12:57:16   Input tokens:  1645 ($0.01316)
2026-04-23 12:57:16   Output tokens: 7064 ($0.21132)
2026-04-23 12:57:16   TOTAL COST: $0.22448
```

## Provider / Model Cheat-Sheet

| Provider | Model | Cost (1024² medium) | Auth |
|---|---|---|---|
| `openrouter` (default) | `openai/gpt-5.4-image-2` | ~$0.22 | `OPENROUTER_API_KEY` |
| `openai` | `gpt-image-2` | ~$0.05 | `OPENAI_API_KEY` + verified org |
| `openai` | `gpt-image-1` | ~$0.03 | `OPENAI_API_KEY` |
| `openai` | `dall-e-3` | $0.04–$0.12 | `OPENAI_API_KEY` |

OpenRouter is the default because it works without OpenAI organization verification — but it's roughly 7× more expensive than direct API for `gpt-image-2`.

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `403 Your organization must be verified` | `gpt-image-2` needs org verification on OpenAI direct | Verify at platform.openai.com → Settings → Organization → General, OR use `--model gpt-image-1`, OR use the default OpenRouter provider |
| `400 invalid model ID` (OpenRouter) | Wrong model name on OpenRouter | OpenRouter calls it `openai/gpt-5.4-image-2`, NOT `openai/gpt-image-2` |
| Image not saved but call succeeded | Response format mismatch | Check `image_response.json` — the script handles both OpenAI's `data[0].b64_json` and OpenRouter's `choices[0].message.images[]` formats |

## License

MIT
