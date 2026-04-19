#!/usr/bin/env python3
"""
FastAPI server — Social → Obsidian via GitHub API (Phase 2 / Railway).

POST /enrich  {"url": "https://..."}
GET  /        health check
"""

import base64
import os
from datetime import date

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from github import Github, GithubException
from pydantic import BaseModel

from enrich import extract_metadata, summarize_with_claude, render_markdown, safe_filename

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN")
GITHUB_REPO       = os.getenv("GITHUB_REPO")          # e.g. "liorshilovsky-ai/obsidian-vault"
GITHUB_VAULT_PATH = os.getenv("GITHUB_VAULT_PATH", "06 - Resources/Social")

app = FastAPI(title="Social → Obsidian", version="2.0")


class EnrichRequest(BaseModel):
    url: str


class EnrichResponse(BaseModel):
    title: str
    category: str
    github_path: str
    message: str


def push_to_github(content: str, clean_title: str) -> str:
    today = date.today().isoformat()
    filename = f"{today} - {safe_filename(clean_title)}.md"
    path = f"{GITHUB_VAULT_PATH}/{filename}"

    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)

    try:
        repo.create_file(
            path=path,
            message=f"social-clip: {clean_title}",
            content=content,
            branch="main",
        )
    except GithubException as e:
        if e.status == 422:
            # file already exists — skip silently
            pass
        else:
            raise

    return path


@app.get("/")
def health():
    return {"status": "ok", "repo": GITHUB_REPO, "path": GITHUB_VAULT_PATH}


@app.post("/enrich", response_model=EnrichResponse)
def enrich(req: EnrichRequest):
    if not req.url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        meta = extract_metadata(req.url)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=f"yt-dlp failed: {e}")

    try:
        ai = summarize_with_claude(meta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Claude failed: {e}")

    md = render_markdown(meta, ai)

    try:
        github_path = push_to_github(md, ai["clean_title"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitHub push failed: {e}")

    return EnrichResponse(
        title=ai["clean_title"],
        category=ai["category"],
        github_path=github_path,
        message=f"✓ Saved: {ai['clean_title']}",
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
