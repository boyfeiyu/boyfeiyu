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
        self.assertIn('1 merged PRs, 1 open PRs', profile.summary_svg(snapshot))

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
        self.assertIn('当前没有符合口径的 PR', profile.archive(snapshot))
        self.assertIn('0 merged PRs, 0 open PRs', profile.summary_svg(snapshot))
        ET.fromstring(profile.summary_svg(snapshot))

    def test_chart_ranks_all_projects_by_merged_then_total_then_name(self):
        nodes = [node(n, repo='org/open-only', draft=True) for n in range(1, 31)]
        nodes += [node(n, repo='org/leader', state='MERGED') for n in range(1, 4)]
        nodes += [node(n, repo='org/runner-up', state='MERGED') for n in range(1, 3)]
        nodes += [node(n, repo='org/runner-up') for n in range(3, 13)]
        for repo, total in [('org/tied-b', 5), ('org/tied-a', 5), ('org/tied-small', 2)]:
            nodes.append(node(repo=repo, state='MERGED'))
            nodes += [node(n, repo=repo) for n in range(2, total + 1)]
        snapshot = self.snapshot(nodes)
        svg = ET.fromstring(profile.summary_svg(snapshot))
        rows = svg.findall('.//{*}g[@aria-label]')
        expected = [('org/leader', 3, 3), ('org/runner-up', 2, 12),
                    ('org/tied-a', 1, 5), ('org/tied-b', 1, 5), ('org/tied-small', 1, 2),
                    ('org/open-only', 0, 30)]
        self.assertEqual([r.findall('{*}text')[0].text for r in rows],
                         [repo for repo, _, _ in expected])
        self.assertEqual([repo for repo, _ in profile.groups(snapshot)],
                         [repo for repo, _, _ in expected])
        for row, (repo, merged, total) in zip(rows, expected):
            self.assertEqual([t.text for t in row.findall('{*}text')],
                             [repo, str(total), str(merged), str(total-merged)])
            self.assertEqual(row.attrib['aria-label'],
                             f'{repo}: {merged} merged PRs, {total-merged} open PRs, {total} total PRs')
            rects = {r.attrib['fill']: r for r in row.findall('{*}rect')}
            if merged:
                self.assertEqual(float(rects['#b69cff'].attrib['width']), merged * 270 / 30)
            else:
                self.assertNotIn('#b69cff', rects)
            if total > merged:
                self.assertEqual(float(rects['#58ead5'].attrib['width']), (total-merged) * 270 / 30)
                self.assertEqual(float(rects['#58ead5'].attrib['x']), 475 + merged * 270 / 30)
            for rect in rects.values():
                self.assertLessEqual(float(rect.attrib['x']) + float(rect.attrib['width']), 745)
        self.assertGreater(int(svg.attrib['height']), 390)
        self.assertEqual(len(snapshot['pullRequests']), len(nodes))
        for pr in snapshot['pullRequests']:
            self.assertIn(pr['url'], profile.archive(snapshot))

    def test_chart_keeps_all_open_only_projects_and_drafts(self):
        nodes = [node(n, repo=f'org/project{total}', draft=True)
                 for total in (2, 10, 4, 7, 3, 1, 6) for n in range(1, total + 1)]
        snapshot = self.snapshot(nodes)
        svg = ET.fromstring(profile.summary_svg(snapshot))
        rows = svg.findall('.//{*}g[@aria-label]')
        self.assertEqual([r.findall('{*}text')[0].text for r in rows],
                         [f'org/project{total}' for total in (10, 7, 6, 4, 3, 2, 1)])
        for row, total in zip(rows, (10, 7, 6, 4, 3, 2, 1)):
            self.assertEqual([t.text for t in row.findall('{*}text')][1:],
                             [str(total), '0', str(total)])
            rects = {r.attrib['fill']: r for r in row.findall('{*}rect')}
            self.assertNotIn('#b69cff', rects)
            self.assertEqual(float(rects['#58ead5'].attrib['width']), total * 27)
        self.assertIn('0 merged PRs, 33 open PRs, 7 projects', profile.summary_svg(snapshot))
        self.assertIn('草稿 / 开放', profile.archive(snapshot))

    def test_chart_with_no_prs_has_explicit_empty_state(self):
        snapshot = profile.fetch_snapshot(lambda _: page([]), now=NOW)
        svg = ET.fromstring(profile.summary_svg(snapshot))
        self.assertEqual(svg.findall('.//{*}g[@aria-label]'), [])
        self.assertIn('No public external PRs yet.', ''.join(svg.itertext()))

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
