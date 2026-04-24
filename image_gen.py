#!/usr/bin/env python3
"""Generate images via OpenAI, OpenRouter, or Google Gemini — one CLI.

Always saves the raw API response to disk before parsing — base64 image data
is irreplaceable, and re-calling costs real money.

Best-effort philosophy: route any model name to a sensible provider, parse
whatever response shape comes back. If auto-detection guesses wrong, pass
`--provider` explicitly.

Usage:
    imagegen "your prompt here"                             # default: openrouter
    imagegen "prompt" --model openai/gpt-5.4-image-2        # auto → openrouter
    imagegen "prompt" --model gpt-image-2                   # auto → openai
    imagegen "prompt" --model gemini-3-pro-image-preview    # auto → gemini
    imagegen "prompt" --model imagen-4.0-generate-preview   # auto → gemini (:predict)
    imagegen --force "regen"                                # bypass cache
    imagegen                                                # re-extract from cache

Env vars:
    IMAGE_PROVIDER      openai | openrouter | gemini  (default: openrouter)
    IMAGE_MODEL         default model override
    OUT_DIR             output directory (default: cwd)
    OPENAI_API_KEY      for --provider openai
    OPENROUTER_API_KEY  for --provider openrouter
    GEMINI_API_KEY      for --provider gemini  (Google AI Studio key)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

OUT_DIR = Path(os.environ.get("OUT_DIR", "."))

DEFAULT_MODELS = {
    "openrouter": "openai/gpt-5.4-image-2",
    "openai": "gpt-image-2",
    "gemini": "gemini-3-pro-image-preview",
}


def detect_provider(model: str) -> str | None:
    """Guess the provider from a model id. Returns None if ambiguous."""
    if "/" in model:
        return "openrouter"  # vendor/model → OpenRouter format
    low = model.lower()
    if low.startswith(("gemini", "imagen")):
        return "gemini"
    if low.startswith(("dall-e", "gpt-image")):
        return "openai"
    return None


def _build_request(provider: str, prompt: str, model: str,
                   size: str | None, quality: str | None) -> tuple[str, dict, dict]:
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                   "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        return url, headers, payload

    if provider == "gemini":
        key = os.environ["GEMINI_API_KEY"]
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        base = "https://generativelanguage.googleapis.com/v1beta/models"
        if model.lower().startswith("imagen"):
            url = f"{base}/{model}:predict"
            payload: dict = {"instances": [{"prompt": prompt}],
                             "parameters": {"sampleCount": 1}}
            if size:
                payload["parameters"]["aspectRatio"] = size  # e.g. "1:1"
        else:
            url = f"{base}/{model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            }
        return url, headers, payload

    # openai
    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
               "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "n": 1}
    if size:
        payload["size"] = size
    if quality:
        payload["quality"] = quality
    return url, headers, payload


def save_raw(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    log.info("Raw response → %s (%d bytes)", path, path.stat().st_size)


_DATA_URL_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/webp;base64,",
)


def _save_data_url(url: str, path: Path) -> Path | None:
    for prefix in _DATA_URL_PREFIXES:
        if url.startswith(prefix):
            path.write_bytes(base64.b64decode(url[len(prefix):]))
            log.info("Saved %s (%d bytes)", path, path.stat().st_size)
            return path
    log.info("Non-base64 image url: %s", url[:120])
    return None


def extract_images(data: dict, out_dir: Path, prefix: str = "image") -> list[Path]:
    """Best-effort extraction across OpenAI, OpenRouter, and Gemini shapes."""
    saved: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    # OpenAI /images/generations: data[].b64_json or data[].url
    for i, item in enumerate(data.get("data") or []):
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if b64:
            p = out_dir / f"{prefix}_{i}.png"
            p.write_bytes(base64.b64decode(b64))
            log.info("Saved %s (%d bytes)", p, p.stat().st_size)
            saved.append(p)
        elif item.get("url"):
            log.info("Image at URL (not downloaded): %s", item["url"])

    # OpenRouter chat completions: choices[0].message.images[].image_url.url
    for ci, choice in enumerate(data.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message") or {}
        for i, img in enumerate(msg.get("images") or []):
            if not isinstance(img, dict):
                continue
            url = (img.get("image_url") or {}).get("url", "")
            p = _save_data_url(url, out_dir / f"{prefix}_or_{ci}_{i}.png")
            if p:
                saved.append(p)

    # Gemini generateContent: candidates[].content.parts[].inlineData
    for ci, cand in enumerate(data.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        parts = (cand.get("content") or {}).get("parts") or []
        for i, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                ext = (inline.get("mimeType") or "image/png").split("/")[-1]
                p = out_dir / f"{prefix}_gm_{ci}_{i}.{ext}"
                p.write_bytes(base64.b64decode(inline["data"]))
                log.info("Saved %s (%d bytes)", p, p.stat().st_size)
                saved.append(p)

    # Gemini Imagen :predict: predictions[].bytesBase64Encoded
    for i, pred in enumerate(data.get("predictions") or []):
        if not isinstance(pred, dict):
            continue
        b64 = pred.get("bytesBase64Encoded")
        if b64:
            ext = (pred.get("mimeType") or "image/png").split("/")[-1]
            p = out_dir / f"{prefix}_im_{i}.{ext}"
            p.write_bytes(base64.b64decode(b64))
            log.info("Saved %s (%d bytes)", p, p.stat().st_size)
            saved.append(p)

    return saved


def log_cost(data: dict) -> None:
    usage = data.get("usage")
    meta = data.get("usageMetadata")  # Gemini field name
    if not usage and not meta:
        log.info("No usage data in response")
        return

    log.info("--- Usage & Cost ---")

    if usage and "cost" in usage:  # OpenRouter: cost computed server-side
        cd = usage.get("cost_details", {}) or {}
        log.info("  Input tokens:  %s ($%s)", usage.get("prompt_tokens"),
                 cd.get("upstream_inference_prompt_cost"))
        log.info("  Output tokens: %s ($%s)", usage.get("completion_tokens"),
                 cd.get("upstream_inference_completions_cost"))
        log.info("  TOTAL COST: $%s", usage["cost"])
        return

    if usage:  # OpenAI: derive from token counts
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        ind = usage.get("input_tokens_details") or {}
        outd = usage.get("output_tokens_details") or {}
        text_in = ind.get("text_tokens", in_tok) or 0
        img_in = ind.get("image_tokens", 0) or 0
        text_out = outd.get("text_tokens", 0) or 0
        img_out = outd.get("image_tokens", out_tok) or 0
        cost = (text_in * 5 + img_in * 8 + text_out * 10 + img_out * 30) / 1_000_000
        log.info("  Text input:   %d × $5/M", text_in)
        log.info("  Image input:  %d × $8/M", img_in)
        log.info("  Text output:  %d × $10/M", text_out)
        log.info("  Image output: %d × $30/M", img_out)
        log.info("  TOTAL COST: $%.6f", cost)
        return

    # Gemini: only token counts, no server-side cost — check Google's pricing page
    log.info("  Prompt tokens:     %s", meta.get("promptTokenCount"))
    log.info("  Candidates tokens: %s", meta.get("candidatesTokenCount"))
    log.info("  Total tokens:      %s", meta.get("totalTokenCount"))
    log.info("  (Gemini: cost not returned — see ai.google.dev/pricing)")


def resolve_provider_and_model(arg_provider: str | None,
                                arg_model: str | None,
                                env_provider: str | None) -> tuple[str, str]:
    """Pick a (provider, model) pair from flags, env, and auto-detection."""
    if arg_provider:
        return arg_provider, arg_model or DEFAULT_MODELS[arg_provider]

    if arg_model:
        guessed = detect_provider(arg_model)
        if guessed:
            return guessed, arg_model
        # Unknown shape — fall back to env or openrouter (it accepts free-form ids)
        provider = env_provider or "openrouter"
        return provider, arg_model

    provider = env_provider or "openrouter"
    return provider, DEFAULT_MODELS.get(provider, DEFAULT_MODELS["openrouter"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("prompt", nargs="?", help="image prompt (omit to re-extract from cache)")
    ap.add_argument("--provider", choices=["openai", "openrouter", "gemini"], default=None,
                    help="override auto-detection (default: auto from model, else openrouter)")
    ap.add_argument("--model", default=os.environ.get("IMAGE_MODEL"),
                    help="model id; auto-routes to provider based on name")
    ap.add_argument("--size", default=None, help="OpenAI size or Imagen aspect ratio")
    ap.add_argument("--quality", default=None, help="OpenAI quality (low/medium/high/auto)")
    ap.add_argument("--force", action="store_true", help="bypass cache, force new API call")
    ap.add_argument("--out", default=str(OUT_DIR), help="output directory")
    args = ap.parse_args()

    out_dir = Path(args.out)
    raw_path = out_dir / "image_response.json"

    # Cache: re-extract from saved JSON for free
    if raw_path.exists() and not args.force and not args.prompt:
        log.info("Re-extracting from cached %s (no API call)", raw_path)
        data = json.loads(raw_path.read_text())
        extract_images(data, out_dir)
        log_cost(data)
        return 0

    if not args.prompt:
        log.error("No prompt provided and no cached response at %s", raw_path)
        return 1

    provider, model = resolve_provider_and_model(
        args.provider, args.model, os.environ.get("IMAGE_PROVIDER"))

    url, headers, payload = _build_request(provider, args.prompt, model, args.size, args.quality)

    log.info("Calling %s (model=%s)...", provider, model)
    start = time.time()
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=600)
    except httpx.HTTPError as exc:
        log.error("HTTP request failed: %s", exc)
        return 1
    log.info("HTTP %d in %.1fs", resp.status_code, time.time() - start)

    # MUST save raw before parsing — base64 data is irreplaceable
    try:
        data = resp.json()
    except ValueError:
        log.error("Non-JSON response: %s", resp.text[:500])
        return 1

    save_raw(data, raw_path)

    if resp.status_code >= 400:
        err = data.get("error", data)
        log.error("API error: %s", json.dumps(err, indent=2)[:1500])
        if resp.status_code == 403 and provider == "openai" and "gpt-image-2" in model:
            log.info("HINT: gpt-image-2 needs org verification. "
                     "Try --model gpt-image-1 or --provider openrouter")
        return 1

    saved = extract_images(data, out_dir)
    if not saved:
        log.warning("No images extracted — check %s manually", raw_path)
    log_cost(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
