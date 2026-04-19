#!/usr/bin/env python3
"""
Social → Obsidian enrichment pipeline (Phase 1 — local).

Usage:
    python enrich.py "<video_url>"

Requires .env with ANTHROPIC_API_KEY and VAULT_TARGET_DIR.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
VAULT_TARGET_DIR = os.getenv("VAULT_TARGET_DIR")

CATEGORIES = ["אוכל ובישול", "טכנולוגיה", "כושר ובריאות", "מוסיקה", "למידה והשכלה", "עסקים וכלכלה", "בידור והומור", "אמנות ועיצוב", "טיולים ונסיעות", "אחר"]

CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def _write_cookies_file() -> str | None:
    """Write YTDLP_COOKIES env var (base64 Netscape format) to a temp file. Returns path or None."""
    raw = os.getenv("YTDLP_COOKIES")
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(decoded)
        f.close()
        return f.name
    except Exception:
        return None


def _oembed_fallback(url: str, platform: str, api_url: str) -> dict:
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return {
        "title": data.get("title", ""),
        "uploader": data.get("author_name", ""),
        "channel": data.get("author_name", ""),
        "description": data.get("title", ""),
        "tags": [],
        "webpage_url": url,
        "original_url": url,
        "extractor_key": platform,
    }


def extract_metadata(url: str) -> dict:
    cookies_path = _write_cookies_file()

    attempts = []
    if cookies_path:
        attempts.append(["--cookies", cookies_path, "--impersonate", "chrome"])
    attempts.append(["--cookies-from-browser", "safari"])
    attempts.append(["--impersonate", "chrome"])
    attempts.append([])

    result = None
    for extra_args in attempts:
        result = subprocess.run(
            ["yt-dlp", "-j", "--skip-download", "--no-playlist"] + extra_args + [url],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)

    if "tiktok.com" in url:
        try:
            return _oembed_fallback(url, "TikTok", f"https://www.tiktok.com/oembed?url={urllib.request.quote(url, safe='')}")
        except Exception as e:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()} | TikTok oEmbed failed: {e}")

    if "instagram.com" in url:
        # Instagram blocks all scraping — return minimal stub so we at least save the link
        return {
            "title": "Instagram Post",
            "uploader": "",
            "channel": "",
            "description": "",
            "tags": [],
            "webpage_url": url,
            "original_url": url,
            "extractor_key": "Instagram",
            "_stub": True,
        }

    raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")


def _stub_ai(url: str) -> dict:
    return {
        "summary": f"קישור Instagram — תוכן לא זמין לעיבוד אוטומטי.\n\n{url}",
        "category": "אחר",
        "clean_title": "Instagram Post",
        "key_tags": ["instagram", "social"],
    }


def summarize_with_claude(meta: dict) -> dict:
    if meta.get("_stub"):
        return _stub_ai(meta["webpage_url"])
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    title = meta.get("title", "")
    description = (meta.get("description") or "")[:1500]
    uploader = meta.get("uploader") or meta.get("channel") or ""
    tags = meta.get("tags") or []
    tags_str = ", ".join(tags[:20]) if tags else "אין"

    prompt = f"""אתה עוזר שמסכם סרטוני רשתות חברתיות לרשימות Obsidian.

פרטי הסרטון:
- כותרת: {title}
- יוצר: {uploader}
- תיאור: {description}
- תגיות: {tags_str}

החזר JSON בלבד (ללא markdown) עם המבנה הזה:
{{
  "summary": "סיכום בעברית של 2-3 משפטים על תוכן הסרטון",
  "category": "קטגוריה אחת מהרשימה: {', '.join(CATEGORIES)}",
  "clean_title": "כותרת נקייה בעברית (עד 60 תווים)",
  "key_tags": ["תגית1", "תגית2", "תגית3"]
}}"""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # strip possible markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def safe_filename(text: str, max_len: int = 60) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = text.strip(". ")
    return text[:max_len]


def render_markdown(meta: dict, ai: dict) -> str:
    today = date.today().isoformat()
    url = meta.get("webpage_url") or meta.get("original_url") or ""
    uploader = meta.get("uploader") or meta.get("channel") or "לא ידוע"
    platform = (meta.get("extractor_key") or "social").lower()
    key_tags = ai.get("key_tags") or []
    tags_yaml = json.dumps(key_tags, ensure_ascii=False)
    tags_inline = " ".join(f"#{t.replace(' ', '_')}" for t in key_tags)

    return f"""---
type: social-clip
source: {platform}
url: {url}
author: "{uploader}"
category: {ai['category']}
date-saved: {today}
tags: {tags_yaml}
---

# {ai['clean_title']}

## סיכום
{ai['summary']}

## פרטים
- **פלטפורמה:** {platform.capitalize()}
- **יוצר:** {uploader}
- **URL מקורי:** [{platform.capitalize()} link]({url})
- **נשמר:** {today}

## תגיות
{tags_inline}
"""


def write_note(content: str, clean_title: str, target_dir: str) -> Path:
    today = date.today().isoformat()
    filename = f"{today} - {safe_filename(clean_title)}.md"
    out_path = Path(target_dir) / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python enrich.py <url>")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    if not VAULT_TARGET_DIR:
        print("Error: VAULT_TARGET_DIR not set in .env")
        sys.exit(1)

    url = sys.argv[1]
    print(f"→ מחלץ metadata מ: {url}")
    meta = extract_metadata(url)
    print(f"  כותרת מקורית: {meta.get('title', '?')}")

    print("→ מסכם עם Claude Haiku...")
    ai = summarize_with_claude(meta)
    print(f"  כותרת עברית: {ai['clean_title']}")
    print(f"  קטגוריה: {ai['category']}")

    md = render_markdown(meta, ai)
    out_path = write_note(md, ai["clean_title"], VAULT_TARGET_DIR)
    print(f"✓ נכתב: {out_path}")


if __name__ == "__main__":
    main()
