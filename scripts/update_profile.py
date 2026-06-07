#!/usr/bin/env python3
"""
Update GitHub profile README without relying on external SVG services.

Features:
1. Sort featured repositories by stars + recent pushed activity.
2. Generate local SVG cards:
   - assets/github-stats.svg
   - assets/top-langs.svg
3. Replace README content between FEATURED_REPOS markers.

Only uses Python standard library.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USERNAME = os.getenv("GITHUB_USERNAME", "yann-yee")
TOKEN = os.getenv("GITHUB_TOKEN", "")
MAX_FEATURED_REPOS = int(os.getenv("MAX_FEATURED_REPOS", "4"))

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ASSETS = ROOT / "assets"
STATS_SVG = ASSETS / "github-stats.svg"
LANGS_SVG = ASSETS / "top-langs.svg"

START = "<!-- FEATURED_REPOS_START -->"
END = "<!-- FEATURED_REPOS_END -->"

API = "https://api.github.com"


def github_get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params)

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{USERNAME}-profile-readme-updater",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {url}\n{body}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {url}\n{exc}") from exc


def fetch_all_repos() -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    for page in range(1, 11):
        chunk = github_get(
            f"/users/{USERNAME}/repos",
            {
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
    return repos


def days_since(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return max(0, (datetime.now(timezone.utc) - dt).days)


def repo_score(repo: dict[str, Any]) -> float:
    """
    Weighted sorting:
    - Star count is the main signal.
    - Recent push activity is the second signal.

    Formula details:
    stars_score = stars * 100
    recency_score = 100 * exp(-days_since_pushed / 45)

    This means:
    - A repo pushed today gets almost +100.
    - A repo pushed 45 days ago gets about +37.
    - One star is still very meaningful, but recent active repos can win among low-star repos.
    """
    stars = repo.get("stargazers_count", 0)
    days = days_since(repo.get("pushed_at") or repo.get("updated_at"))
    recency_score = 100 * math.exp(-days / 45)
    return stars * 100 + recency_score


def clean_repo_desc(desc: str | None) -> str:
    if not desc:
        return "No description yet."
    return desc.strip().replace("\n", " ")


def language_badge(language: str | None) -> str:
    return f"`{language or 'Unknown'}`"


def build_featured_repos(repos: list[dict[str, Any]]) -> str:
    candidates = [
        r
        for r in repos
        if not r.get("fork")
        and not r.get("archived")
        and r.get("name") != USERNAME
    ]
    selected = sorted(candidates, key=repo_score, reverse=True)[:MAX_FEATURED_REPOS]

    if not selected:
        return "暂无可展示仓库。"

    lines: list[str] = []
    for repo in selected:
        name = repo["name"]
        url = repo["html_url"]
        desc = clean_repo_desc(repo.get("description"))
        lang = language_badge(repo.get("language"))
        stars = repo.get("stargazers_count", 0)
        pushed = (repo.get("pushed_at") or repo.get("updated_at") or "")[:10]

        lines.append(
            f"- **[{name}]({url})** {lang} · ⭐ {stars} · 最近提交 `{pushed}`  \n"
            f"  {desc}"
        )

    return "\n".join(lines)


def update_readme(featured_markdown: str) -> None:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(rf"{re.escape(START)}.*?{re.escape(END)}", re.DOTALL)
    replacement = f"{START}\n{featured_markdown}\n{END}"

    if pattern.search(text):
        text = pattern.sub(replacement, text)
    else:
        text += f"\n\n## 📌 Featured Repositories\n\n{replacement}\n"

    README.write_text(text, encoding="utf-8")


def total_stars(repos: list[dict[str, Any]]) -> int:
    return sum(int(r.get("stargazers_count", 0)) for r in repos if not r.get("fork"))


def total_forks(repos: list[dict[str, Any]]) -> int:
    return sum(int(r.get("forks_count", 0)) for r in repos if not r.get("fork"))


def recent_active_count(repos: list[dict[str, Any]], days: int = 90) -> int:
    return sum(
        1
        for r in repos
        if not r.get("fork")
        and not r.get("archived")
        and days_since(r.get("pushed_at") or r.get("updated_at")) <= days
    )


def svg_text(x: int, y: int, content: str, size: int = 14, weight: str = "400", color: str = "#d8e2ef") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="Segoe UI, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">{html.escape(str(content))}</text>'
    )


def generate_stats_svg(user: dict[str, Any], repos: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d UTC")
    public_repos = user.get("public_repos", len(repos))
    followers = user.get("followers", 0)
    stars = total_stars(repos)
    forks = total_forks(repos)
    active = recent_active_count(repos, 90)

    items = [
        ("Public Repos", public_repos),
        ("Total Stars", stars),
        ("Total Forks", forks),
        ("Followers", followers),
        ("Active 90d", active),
    ]

    rows = []
    y = 84
    for label, value in items:
        rows.append(svg_text(32, y, label, 14, "500", "#a9b8ca"))
        rows.append(svg_text(300, y, value, 16, "700", "#7dd3fc"))
        y += 29

    return f'''<svg width="420" height="240" viewBox="0 0 420 240" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub stats for {html.escape(USERNAME)}">
  <rect width="420" height="240" rx="16" fill="#0b1220"/>
  <rect x="1" y="1" width="418" height="238" rx="15" stroke="#24364f"/>
  <circle cx="356" cy="38" r="72" fill="#0ea5e9" opacity="0.10"/>
  <circle cx="52" cy="210" r="82" fill="#8b5cf6" opacity="0.08"/>
  {svg_text(28, 42, "📊 GitHub Stats", 22, "700", "#f8fafc")}
  {svg_text(28, 62, f"@{USERNAME} · updated {now}", 11, "400", "#7c8da5")}
  {''.join(rows)}
</svg>
'''


def fetch_language_totals(repos: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for repo in repos:
        if repo.get("fork") or repo.get("archived"):
            continue
        name = repo["name"]
        try:
            langs = github_get(f"/repos/{USERNAME}/{name}/languages")
        except RuntimeError as exc:
            print(f"warning: skip languages for {name}: {exc}", file=sys.stderr)
            continue
        for lang, size in langs.items():
            totals[lang] = totals.get(lang, 0) + int(size)
    return totals


def generate_langs_svg(language_totals: dict[str, int]) -> str:
    sorted_langs = sorted(language_totals.items(), key=lambda x: x[1], reverse=True)[:6]
    total = sum(v for _, v in sorted_langs) or 1

    if not sorted_langs:
        sorted_langs = [("No data", 1)]
        total = 1

    palette = ["#7dd3fc", "#a78bfa", "#34d399", "#facc15", "#fb7185", "#f97316"]
    y = 82
    rows = []
    for idx, (lang, value) in enumerate(sorted_langs):
        pct = value / total * 100
        bar_w = max(4, int(230 * value / max(v for _, v in sorted_langs)))
        color = palette[idx % len(palette)]
        rows.append(svg_text(32, y, lang, 14, "600", "#d8e2ef"))
        rows.append(svg_text(318, y, f"{pct:.1f}%", 13, "600", "#a9b8ca"))
        rows.append(f'<rect x="32" y="{y + 8}" width="230" height="8" rx="4" fill="#1f2a3d"/>')
        rows.append(f'<rect x="32" y="{y + 8}" width="{bar_w}" height="8" rx="4" fill="{color}"/>')
        y += 28

    return f'''<svg width="420" height="240" viewBox="0 0 420 240" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Top languages for {html.escape(USERNAME)}">
  <rect width="420" height="240" rx="16" fill="#0b1220"/>
  <rect x="1" y="1" width="418" height="238" rx="15" stroke="#24364f"/>
  <circle cx="338" cy="50" r="78" fill="#22c55e" opacity="0.09"/>
  <circle cx="84" cy="222" r="90" fill="#0ea5e9" opacity="0.08"/>
  {svg_text(28, 42, "🧩 Top Languages", 22, "700", "#f8fafc")}
  {svg_text(28, 62, "Aggregated from public non-fork repositories", 11, "400", "#7c8da5")}
  {''.join(rows)}
</svg>
'''


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    user = github_get(f"/users/{USERNAME}")
    repos = fetch_all_repos()

    featured = build_featured_repos(repos)
    update_readme(featured)

    STATS_SVG.write_text(generate_stats_svg(user, repos), encoding="utf-8")
    language_totals = fetch_language_totals(repos)
    LANGS_SVG.write_text(generate_langs_svg(language_totals), encoding="utf-8")

    print("Updated README and local SVG stats successfully.")


if __name__ == "__main__":
    main()
