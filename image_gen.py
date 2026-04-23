#!/usr/bin/env python3
"""Generate images via OpenAI gpt-image-* or OpenRouter gpt-5.4-image-2.

Always saves the raw API response to disk before parsing — base64 image data
is irreplaceable, and re-calling costs real money.

Usage:
    python image_gen.py "your prompt here"
    python image_gen.py "your prompt here" --provider openrouter
    python image_gen.py --force "regenerate"  # bypass cache
    python image_gen.py  # re-extract from cached image_response.json

Env vars:
    IMAGE_PROVIDER   "openai" (default) or "openrouter"
    IMAGE_MODEL      override model id
    OUT_DIR          output directory (default: cwd)
    OPENAI_API_KEY   required for openai provider
    OPENROUTER_API_KEY  required for openrouter provider
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
RAW_PATH = OUT_DIR / "image_response.json"


def _build_request(provider: str, prompt: str, model: str, size: str, quality: str) -> tuple[str, dict, dict]:
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                   "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    else:
        url = "https://api.openai.com/v1/images/generations"
        headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                   "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "quality": quality}
    return url, headers, payload


def save_raw(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    log.info("Raw response → %s (%d bytes)", path, path.stat().st_size)


def _save_data_url(url: str, path: Path) -> Path | None:
    for prefix in ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,"):
        if url.startswith(prefix):
            path.write_bytes(base64.b64decode(url[len(prefix):]))
            log.info("Saved %s (%d bytes)", path, path.stat().st_size)
            return path
    log.info("Non-base64 image url: %s", url[:120])
    return None


def extract_images(data: dict, out_dir: Path, prefix: str = "image") -> list[Path]:
    saved: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    # OpenAI /images/generations: data[].b64_json or data[].url
    for i, item in enumerate(data.get("data", []) or []):
        b64 = item.get("b64_json")
        if b64:
            p = out_dir / f"{prefix}_{i}.png"
            p.write_bytes(base64.b64decode(b64))
            log.info("Saved %s (%d bytes)", p, p.stat().st_size)
            saved.append(p)
        elif item.get("url"):
            log.info("Image at URL (not downloaded): %s", item["url"])

    # OpenRouter chat completions: choices[0].message.images[].image_url.url
    msg = ((data.get("choices") or [{}])[0]).get("message", {})
    for i, img in enumerate(msg.get("images") or []):
        url = (img.get("image_url") or {}).get("url", "")
        p = _save_data_url(url, out_dir / f"{prefix}_or_{i}.png")
        if p:
            saved.append(p)

    return saved


def log_cost(data: dict) -> None:
    usage = data.get("usage")
    if not usage:
        log.info("No usage data in response")
        return

    log.info("--- Usage & Cost ---")
    if "cost" in usage:  # OpenRouter: cost computed server-side
        cd = usage.get("cost_details", {})
        log.info("  Input tokens:  %s ($%s)", usage.get("prompt_tokens"),
                 cd.get("upstream_inference_prompt_cost"))
        log.info("  Output tokens: %s ($%s)", usage.get("completion_tokens"),
                 cd.get("upstream_inference_completions_cost"))
        log.info("  TOTAL COST: $%s", usage["cost"])
    else:  # OpenAI: derive from token counts
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", help="image prompt (omit to re-extract from cache)")
    ap.add_argument("--provider", choices=["openai", "openrouter"],
                    default=os.environ.get("IMAGE_PROVIDER", "openrouter"))
    ap.add_argument("--model", default=os.environ.get("IMAGE_MODEL"))
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--force", action="store_true", help="bypass cache, force new API call")
    ap.add_argument("--out", default=str(OUT_DIR), help="output directory")
    args = ap.parse_args()

    out_dir = Path(args.out)
    raw_path = out_dir / "image_response.json"

    # Cache: re-extract from saved JSON for free
    if raw_path.exists() and not args.force:
        log.info("Using cached response from %s (use --force to re-call API)", raw_path)
        data = json.loads(raw_path.read_text())
        extract_images(data, out_dir)
        log_cost(data)
        return 0

    if not args.prompt:
        log.error("No prompt provided and no cached response at %s", raw_path)
        return 1

    model = args.model or ("openai/gpt-5.4-image-2" if args.provider == "openrouter" else "gpt-image-2")
    url, headers, payload = _build_request(args.provider, args.prompt, model, args.size, args.quality)

    log.info("Calling %s (model=%s)...", args.provider, model)
    start = time.time()
    resp = httpx.post(url, headers=headers, json=payload, timeout=300)
    log.info("HTTP %d in %.1fs", resp.status_code, time.time() - start)

    # MUST save raw before parsing — base64 data is irreplaceable
    try:
        data = resp.json()
    except Exception:
        log.error("Non-JSON response: %s", resp.text[:500])
        return 1

    save_raw(data, raw_path)

    if resp.status_code >= 400:
        log.error("API error: %s", json.dumps(data.get("error", data), indent=2))
        if resp.status_code == 403 and args.provider == "openai" and "gpt-image-2" in model:
            log.info("HINT: gpt-image-2 needs org verification. Try --model gpt-image-1 or --provider openrouter")
        return 1

    saved = extract_images(data, out_dir)
    if not saved:
        log.warning("No images extracted — check %s manually", raw_path)
    log_cost(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
