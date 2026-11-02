#!/usr/bin/env python3
"""Fetch authored public external PRs and render a traceable profile snapshot.

Only the Python standard library and GitHub CLI (preinstalled on Actions) are
required. API errors or incomplete pagination fail before touching any files.
"""

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
USER = "boyfeiyu"
START = "<!-- CONTRIBUTIONS:START -->"
END = "<!-- CONTRIBUTIONS:END -->"
QUERY = """query($login: String!, $cursor: String) {
  user(login: $login) {
    pullRequests(first: 100, after: $cursor, states: [OPEN, MERGED],
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url state isDraft createdAt updatedAt mergedAt
        author { login }
        repository { nameWithOwner isPrivate owner { login } }
      }
    }
  }
}"""


def graphql(variables):
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": QUERY, "variables": variables}),
        text=True, capture_output=True, timeout=90,
    )
    if result.returncode:
        raise ValueError("GitHub API request failed; snapshot left unchanged. Check gh authentication and rate limits.")
    response = json.loads(result.stdout)
    if response.get("errors"):
        raise ValueError("GraphQL returned errors; partial data will not be published.")
    return response


def fetch_snapshot(request=graphql, now=None):
    cursor = None
    seen_cursors = set()
    nodes = []
    total = None
    for _ in range(100):
        response = request({"login": USER, "cursor": cursor})
        if response.get("errors"):
            raise ValueError("GraphQL returned errors; partial data will not be published.")
        connection = response["data"]["user"]["pullRequests"]
        if total is not None and total != connection["totalCount"]:
            raise ValueError("PR count changed during pagination; retry a fresh snapshot.")
        total = connection["totalCount"]
        nodes.extend(connection["nodes"])
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
        if not cursor or cursor in seen_cursors:
            raise ValueError("Pagination did not advance; refusing an incomplete snapshot.")
        seen_cursors.add(cursor)
    else:
        raise ValueError("Pagination limit reached; refusing an incomplete snapshot.")
    if len(nodes) != total or len({n["url"] for n in nodes}) != total:
        raise ValueError("Incomplete or duplicate pagination; retry a fresh snapshot.")

    prs = []
    for node in nodes:
        repo = node["repository"]
        # Never save private repository metadata, even when using a personal token.
        if repo["isPrivate"] or repo["owner"]["login"].lower() == USER.lower():
            continue
        if node["author"]["login"].lower() != USER.lower():
            raise ValueError("PR author does not match the requested user.")
        prs.append({
            "repository": repo["nameWithOwner"],
            **{key: node[key] for key in (
                "number", "title", "url", "state", "isDraft", "createdAt", "updatedAt", "mergedAt"
            )},
        })
    snapshot = {
        "schemaVersion": 1,
        "username": USER,
        "fetchedAt": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": "Authored public PRs in repositories not owned by boyfeiyu; OPEN and MERGED only. Closed unmerged PRs are excluded.",
        "source": "https://api.github.com/graphql",
        "pullRequests": sorted(prs, key=lambda p: (p["updatedAt"], p["url"]), reverse=True),
    }
    validate(snapshot)
    return snapshot


def validate(snapshot):
    if snapshot["schemaVersion"] != 1 or snapshot["username"] != USER:
        raise ValueError("Unexpected snapshot schema or user.")
    datetime.strptime(snapshot["fetchedAt"], "%Y-%m-%dT%H:%M:%SZ")
    seen = set()
    for pr in snapshot["pullRequests"]:
        repo = pr["repository"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid repository name.")
        if repo.split("/")[0].lower() == USER.lower():
            raise ValueError("Own repositories are outside the contribution scope.")
        if type(pr["number"]) is not int or pr["number"] <= 0:
            raise ValueError("Invalid PR number.")
        if pr["url"] != f"https://github.com/{repo}/pull/{pr['number']}" or pr["url"] in seen:
            raise ValueError("Noncanonical or duplicate PR source link.")
        seen.add(pr["url"])
        if pr["state"] not in ("OPEN", "MERGED") or type(pr["isDraft"]) is not bool:
            raise ValueError("Unexpected PR status.")
        if bool(pr["mergedAt"]) != (pr["state"] == "MERGED"):
            raise ValueError("PR state and merge timestamp disagree.")
        if pr["state"] == "MERGED" and pr["isDraft"]:
            raise ValueError("Merged PR cannot be a draft.")
        if not isinstance(pr["title"], str) or not pr["title"].strip():
            raise ValueError("Missing PR title.")
        for key in ("createdAt", "updatedAt", "mergedAt"):
            if pr[key]:
                datetime.strptime(pr[key], "%Y-%m-%dT%H:%M:%SZ")


def md(value):
    # API titles are untrusted Markdown, including pipes and image/HTML syntax.
    value = escape(" ".join(value.split()))
    return re.sub(r"([\\`*_{\}\[\]()|~#!])", r"\\\1", value)


def pr_link(pr):
    return f"[#{pr['number']} · {md(pr['title'])}]({pr['url']})"


def status(pr):
    return "已合并" if pr["state"] == "MERGED" else ("草稿 / 开放" if pr["isDraft"] else "开放")


def groups(snapshot):
    result = {}
    for pr in snapshot["pullRequests"]:
        result.setdefault(pr["repository"], []).append(pr)
    # A stable project order: most contributions, then repository name.
    return sorted(result.items(), key=lambda pair: (-len(pair[1]), pair[0].lower()))


def summary_svg(snapshot):
    prs = snapshot["pullRequests"]
    merged = sum(p["state"] == "MERGED" for p in prs)
    opened = len(prs) - merged
    projects = len(groups(snapshot))
    stamp = snapshot["fetchedAt"].replace("T", " ").replace("Z", " UTC")
    bars = []
    for i, (repo, items) in enumerate(groups(snapshot)[:5]):
        y = 205 + i * 29
        m = sum(p["state"] == "MERGED" for p in items)
        # Keep every nonzero segment visible, and scale to the largest project.
        scale = 270 / max(1, len(groups(snapshot)[0][1]))
        bars.append(f'<text x="44" y="{y}" fill="#b5c6dd" font-size="14">{escape(repo)}</text>')
        bars.append(f'<rect x="475" y="{y-11}" width="270" height="9" rx="4" fill="#1e2d42"/>')
        if m:
            bars.append(f'<rect x="475" y="{y-11}" width="{m*scale:.1f}" height="9" fill="#b69cff"/>')
        if len(items) > m:
            bars.append(f'<rect x="{475+m*scale:.1f}" y="{y-11}" width="{(len(items)-m)*scale:.1f}" height="9" fill="#58ead5"/>')
        bars.append(f'<text x="770" y="{y}" fill="#edf5ff" font-size="14">{len(items)}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="390" viewBox="0 0 960 390" role="img" aria-labelledby="title desc">
  <title id="title">Open source: {merged} merged PRs, {opened} open PRs, {projects} projects</title>
  <desc id="desc">Public external pull requests by boyfeiyu. Purple means merged; mint means open, including drafts. Snapshot: {stamp}.</desc>
  <rect x=".5" y=".5" width="959" height="389" rx="18" fill="#0d1726" stroke="#2a3c54"/>
  <g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">
    <text x="44" y="40" fill="#8fa7c2" font-size="12" letter-spacing="2">PUBLIC CONTRIBUTIONS / VERIFIED AT SOURCE</text>
    <text x="44" y="111" fill="#b69cff" font-size="48" font-weight="700">{merged:02}</text>
    <text x="135" y="108" fill="#c9d6e7" font-size="15">MERGED</text>
    <text x="320" y="111" fill="#58ead5" font-size="48" font-weight="700">{opened:02}</text>
    <text x="411" y="108" fill="#c9d6e7" font-size="15">OPEN</text>
    <text x="597" y="111" fill="#edf5ff" font-size="48" font-weight="700">{projects:02}</text>
    <text x="688" y="108" fill="#c9d6e7" font-size="15">PROJECTS</text>
    <path d="M44 143H916" stroke="#2a3c54"/>
    <text x="44" y="174" fill="#8fa7c2" font-size="11" letter-spacing="1">TOP PROJECTS BY PR COUNT</text>
    <text x="475" y="174" fill="#b69cff" font-size="11">MERGED</text>
    <text x="548" y="174" fill="#58ead5" font-size="11">OPEN</text>
    {''.join(bars)}
    <text x="44" y="366" fill="#8fa7c2" font-size="12">UPDATED {stamp} · github.com/boyfeiyu</text>
  </g>
</svg>
'''


def readme_section(snapshot):
    prs = snapshot["pullRequests"]
    merged = sum(pr["state"] == "MERGED" for pr in prs)
    rows = [
        '[![公开外部 PR 概览：已合并与开放状态分别统计](assets/contributions.svg)](docs/contributions.md)',
        '',
        f'**{merged} 条已合并 · {len(prs)-merged} 条开放（含草稿） · {len(groups(snapshot))} 个项目**',
        '',
        f'数据核验于 **{snapshot["fetchedAt"].replace("T", " ").replace("Z", " UTC")}**，每天自动刷新。',
        '仅统计本人发起的公开外部 PR；开放状态表示尚未合并。数字是当前快照，不代表完整历史贡献量。',
        '',
    ]
    if not prs:
        rows.append('当前没有符合口径的 PR。')
    for repo, items in groups(snapshot)[:4]:
        merged_prs = [p for p in items if p['state'] == 'MERGED']
        open_prs = [p for p in items if p['state'] == 'OPEN']
        rows.extend([
            f'#### [{repo}](https://github.com/{repo})',
            '',
            f'{len(merged_prs)} 条已合并 / {len(open_prs)} 条开放。以下为各状态最近更新的 PR，标题来自 GitHub。',
            '',
            '| 状态 | 具体贡献 / PR 原始标题 |',
            '| :-- | :-- |',
        ])
        for pr in merged_prs[:2] + open_prs[:2]:
            rows.append(f'| {status(pr)} | {pr_link(pr)} |')
        rows.append('')
    rows.extend([
        '[查看全部项目与 PR →](docs/contributions.md) · [原始数据 JSON](data/contributions.json) · '
        '[GitHub 实时记录](https://github.com/pulls?q=is%3Apr+author%3Aboyfeiyu+is%3Apublic)',
    ])
    return '\n'.join(rows)


def archive(snapshot):
    rows = [
        '# 公开开源贡献 / Public contributions', '',
        f'数据核验时间：**{snapshot["fetchedAt"]}**。', '',
        '来源：GitHub GraphQL `user(login: "boyfeiyu").pullRequests`，逐页读取至末页。',
        '口径：本人发起、公开、仓库所有者不是 boyfeiyu、状态为已合并或开放（含草稿）的 PR。',
        '关闭且未合并的 PR 不在本清单内；私有仓库与本人仓库不计入。合并状态不能由 PR 标题推断。', '',
        '[返回主页](../README.md) · [原始快照](../data/contributions.json) · [刷新说明](automation.md)', '',
    ]
    for repo, items in groups(snapshot):
        rows.extend([f'## [{repo}](https://github.com/{repo})', '',
                     '| 状态 | 具体贡献 / PR 原始标题 | 最近更新（UTC） | 合并日期（UTC） |',
                     '| :-- | :-- | :-- | :-- |'])
        for pr in items:
            rows.append(f'| {status(pr)} | {pr_link(pr)} | {pr["updatedAt"][:10]} | {(pr["mergedAt"] or "—")[:10]} |')
        rows.append('')
    if not snapshot['pullRequests']:
        rows.append('当前没有符合口径的 PR。')
    return '\n'.join(rows).rstrip() + '\n'


def render(snapshot, root=ROOT, check=False):
    validate(snapshot)
    readme = (root / 'README.md').read_text(encoding='utf-8')
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) > readme.index(END):
        raise ValueError('README contribution markers are missing, duplicated or reversed.')
    before, rest = readme.split(START)
    _, after = rest.split(END)
    outputs = {
        'README.md': before + START + '\n\n' + readme_section(snapshot) + '\n\n' + END + after,
        'docs/contributions.md': archive(snapshot),
        'assets/contributions.svg': summary_svg(snapshot),
        'data/contributions.json': json.dumps(snapshot, indent=2, ensure_ascii=False) + '\n',
    }
    if check:
        stale = [name for name, content in outputs.items()
                 if not (root / name).exists() or (root / name).read_text(encoding='utf-8') != content]
        if stale:
            raise ValueError('Generated files differ from the saved snapshot: ' + ', '.join(stale))
        return
    for name, content in outputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Render an existing normalized snapshot without API access')
    parser.add_argument('--check', action='store_true', help='Check files against the stored snapshot, without API access')
    args = parser.parse_args()
    if args.check or args.snapshot:
        snapshot = json.loads((args.snapshot or ROOT / 'data/contributions.json').read_text(encoding='utf-8'))
    else:
        snapshot = fetch_snapshot()
    render(snapshot, check=args.check)
    print(f'{"Verified" if args.check else "Updated"} {len(snapshot["pullRequests"])} public external PRs at {snapshot["fetchedAt"]}.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Contribution refresh failed: {error}', file=sys.stderr)
        sys.exit(1)
