import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


SPEC = importlib.util.spec_from_file_location(
    'contributions', Path(__file__).resolve().parents[1] / 'scripts/update_contributions.py'
)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
NOW = '2026-11-02T08:00:00Z'


def node(number=1, repo='upstream/project', state='OPEN', private=False, draft=False):
    return {
        'number': number, 'title': 'Fix edge case',
        'url': f'https://github.com/{repo}/pull/{number}', 'state': state,
        'isDraft': draft, 'createdAt': NOW, 'updatedAt': NOW,
        'mergedAt': NOW if state == 'MERGED' else None,
        'author': {'login': 'boyfeiyu'},
        'repository': {'nameWithOwner': repo, 'isPrivate': private,
                       'owner': {'login': repo.split('/')[0]}},
    }


def page(nodes, total=None, next_cursor=None):
    return {'data': {'user': {'pullRequests': {
        'nodes': nodes, 'totalCount': len(nodes) if total is None else total,
        'pageInfo': {'hasNextPage': next_cursor is not None, 'endCursor': next_cursor},
    }}}}


class ContributionTests(unittest.TestCase):
    def snapshot(self, nodes=None):
        return profile.fetch_snapshot(lambda _: page(nodes or [node()]), now=NOW)

    def test_complete_pagination_and_private_own_repo_exclusion(self):
        calls = []
        pages = [page([node(1), node(2, private=True)], total=4, next_cursor='next'),
                 page([node(3, repo='boyfeiyu/own'), node(4, state='MERGED')], total=4)]

        def request(variables):
            calls.append(variables['cursor'])
            return pages.pop(0)

        snapshot = profile.fetch_snapshot(request, now=NOW)
        self.assertEqual(calls, [None, 'next'])
        self.assertEqual({p['number'] for p in snapshot['pullRequests']}, {1, 4})
        self.assertNotIn('isPrivate', json.dumps(snapshot))
        section = profile.readme_section(snapshot)
        self.assertIn('1 条已合并 · 1 条开放', section)

    def test_incomplete_and_duplicate_pages_fail(self):
        for response in (page([node()], total=2), page([node(), node()])):
            with self.subTest(response=response), self.assertRaises(ValueError):
                profile.fetch_snapshot(lambda _: response)

    def test_repeating_cursor_and_changing_totals_fail(self):
        with self.assertRaisesRegex(ValueError, 'Pagination did not advance'):
            profile.fetch_snapshot(lambda _: page([node()], total=3, next_cursor='repeat'))
        pages = [page([node()], total=2, next_cursor='next'), page([node(2)], total=3)]
        with self.assertRaisesRegex(ValueError, 'count changed'):
            profile.fetch_snapshot(lambda _: pages.pop(0))

    def test_graphql_partial_error_does_not_publish(self):
        response = page([node()])
        response['errors'] = [{'message': 'rate limited'}]
        with self.assertRaisesRegex(ValueError, 'partial data'):
            profile.fetch_snapshot(lambda _: response)

    def test_gh_failure_is_not_an_empty_success(self):
        with patch.object(profile.subprocess, 'run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'snapshot left unchanged'):
                profile.graphql({'login': 'boyfeiyu', 'cursor': None})

    def test_untrusted_titles_escape_markdown_html_and_newlines(self):
        pr = node()
        pr['title'] = 'Fix | [click](https://evil.test) <img src=x> ![image](url)\nnew row'
        section = profile.archive(self.snapshot([pr]))
        self.assertIn(r'Fix \| \[click\]\(https://evil.test\)', section)
        self.assertIn('&lt;img src=x&gt;', section)
        self.assertNotIn('<img src=x>', section)
        self.assertNotIn('![image]', section)
        self.assertNotIn('\nnew row', section)

    def test_draft_is_open_and_merge_state_requires_timestamp(self):
        snapshot = self.snapshot([node(draft=True), node(2, state='MERGED')])
        section = profile.archive(snapshot)
        self.assertIn('| 草稿 / 开放 |', section)
        self.assertIn('| 已合并 |', section)
        next(p for p in snapshot['pullRequests'] if p['state'] == 'OPEN')['mergedAt'] = NOW
        with self.assertRaises(ValueError):
            profile.validate(snapshot)

    def test_noncanonical_url_and_wrong_author_fail(self):
        pr = node()
        pr['url'] = 'https://evil.test/pull/1'
        with self.assertRaisesRegex(ValueError, 'source link'):
            self.snapshot([pr])
        pr = node()
        pr['author']['login'] = 'someone-else'
        with self.assertRaisesRegex(ValueError, 'author'):
            self.snapshot([pr])

    def test_render_is_reproducible_and_preserves_personal_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = 'Personal intro\n' + profile.START + '\nold\n' + profile.END + '\nFooter\n'
            (root / 'README.md').write_text(before)
            snapshot = self.snapshot([node(), node(2, state='MERGED')])
            profile.render(snapshot, root)
            profile.render(snapshot, root, check=True)
            self.assertTrue((root / 'README.md').read_text().startswith('Personal intro\n'))
            self.assertTrue((root / 'README.md').read_text().endswith('\nFooter\n'))
            self.assertEqual(ET.parse(root / 'assets/contributions.svg').getroot().tag,
                             '{http://www.w3.org/2000/svg}svg')
            archive = (root / 'docs/contributions.md').read_text()
            for pr in snapshot['pullRequests']:
                self.assertIn(pr['url'], archive)
            (root / 'assets/contributions.svg').write_text('stale')
            with self.assertRaisesRegex(ValueError, 'assets/contributions.svg'):
                profile.render(snapshot, root, check=True)

    def test_invalid_markers_fail_before_any_writes(self):
        for markers in ('No markers', profile.END + profile.START,
                        profile.START + profile.START + profile.END):
            with self.subTest(markers=markers), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'README.md').write_text(markers)
                with self.assertRaises(ValueError):
                    profile.render(self.snapshot(), root)
                self.assertEqual((root / 'README.md').read_text(), markers)
                self.assertFalse((root / 'data').exists())

    def test_empty_snapshot_renders_explicit_empty_state(self):
        snapshot = profile.fetch_snapshot(lambda _: page([]), now=NOW)
        self.assertIn('当前没有符合口径的 PR', profile.readme_section(snapshot))
        self.assertIn('0 条已合并 · 0 条开放', profile.readme_section(snapshot))
        ET.fromstring(profile.summary_svg(snapshot))

    def test_readme_keeps_summary_image_and_archive_keeps_every_pr(self):
        snapshot = self.snapshot([node(n, repo=f'org/project{n % 6}') for n in range(1, 31)])
        section = profile.readme_section(snapshot)
        self.assertIn('(assets/contributions.svg)', section)
        self.assertIn('(docs/contributions.md)', section)
        self.assertNotIn('#### [', section)
        self.assertFalse(any(line.startswith('|') for line in section.splitlines()))
        archive = profile.archive(snapshot)
        for pr in snapshot['pullRequests']:
            self.assertNotIn(pr['url'], section)
            self.assertIn(pr['url'], archive)


if __name__ == '__main__':
    unittest.main()
