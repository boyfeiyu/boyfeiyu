import copy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


SPEC = importlib.util.spec_from_file_location(
    'snake', Path(__file__).resolve().parents[1] / 'scripts/generate_snake.py')
snake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snake)


def calendar(active=True):
    weeks = []
    for i in range(370):
        if i % 7 == 0:
            weeks.append({'contributionDays': []})
        count = 5 if active and i % 5 == 0 else 0
        weeks[-1]['contributionDays'].append({
            'date': (date(2025, 11, 16) + timedelta(days=i)).isoformat(),
            'weekday': i % 7, 'contributionCount': count,
            'contributionLevel': 'FOURTH_QUARTILE' if count else 'NONE',
        })
    return {'username': 'boyfeiyu', 'fetchedAt': '2026-11-20T11:00:00Z',
            'weeks': weeks, 'totalContributions': 370 if active else 0}


class SnakeTests(unittest.TestCase):
    def test_grows_once_per_eaten_cell_and_preserves_tail_on_growth(self):
        food = {(0, 0), (0, 2), (1, 6), (1, 5)}
        route = snake.route_for_food(food, 2)
        self.assertEqual(route[0], (0, 0))
        frames = snake.simulate(food, route)
        self.assertEqual(len(frames[0].body), 3)
        self.assertEqual(frames[0].eaten, frozenset())
        for point, previous, current in zip(route, frames, frames[1:]):
            grows = point in food and point not in previous.eaten
            self.assertEqual(len(current.body), len(previous.body) + int(grows))
            self.assertEqual(current.body[1:], previous.body if grows else previous.body[:-1])
            self.assertEqual(len(set(current.body)), len(current.body))
            self.assertTrue(all(sum(abs(x-y) for x, y in zip(a, b)) == 1
                                for a, b in zip(current.body, current.body[1:])))
        self.assertEqual(len(frames[-1].body), 7)
        self.assertEqual(frames[-1].eaten, food)
        # A new cycle restores both the short initial snake and unconsumed food.
        self.assertEqual(snake.simulate(food, route), frames)

    def test_empty_and_previously_eaten_cells_never_grow(self):
        route = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
        frames = snake.simulate({(0, 0)}, route)
        self.assertEqual([len(f.body) for f in frames], [3, 4, 4, 4, 4, 4])
        self.assertEqual(len(frames[-1].eaten), 1)
        self.assertTrue(all(len(f.body) == 3 for f in snake.simulate(set(), route)))

    def test_dense_route_covers_all_food_with_only_neighbor_moves(self):
        food = {(x, y) for x in range(53) for y in range(7)}
        route = snake.route_for_food(food, 53)
        self.assertEqual(len(route), 53 * 7)
        self.assertEqual(len(set(route)), len(route))
        x, y = route[0]
        frames = snake.simulate(food, route, start=(x - 1, y))
        self.assertEqual(len(frames[-1].body), 3 + len(route))
        with self.assertRaisesRegex(ValueError, 'adjacent'):
            snake.simulate(set(), [(5, 5)])
        with self.assertRaisesRegex(ValueError, 'collide'):
            snake.simulate(set(), [(-2, 0)])

    def test_sparse_route_chooses_column_directions_and_skips_empty_detours(self):
        food = {(4, 2), (4, 5), (5, 1), (5, 4), (8, 1)}
        route = snake.route_for_food(food, 53)
        self.assertEqual(route, [(4, y) for y in range(2, 6)] +
                                [(5, y) for y in range(5, 0, -1)] +
                                [(6, 1), (7, 1), (8, 1)])

    def test_calendar_accepts_padded_week_and_rejects_partial_data(self):
        original = calendar()
        snake.validate_calendar(original)
        bad = copy.deepcopy(original)
        bad['weeks'][5]['contributionDays'].pop()
        with self.assertRaises(ValueError):
            snake.validate_calendar(bad)
        bad = copy.deepcopy(original)
        bad['weeks'][2] = copy.deepcopy(bad['weeks'][1])
        with self.assertRaises(ValueError):
            snake.validate_calendar(bad)
        bad = copy.deepcopy(original)
        bad['totalContributions'] += 1
        with self.assertRaisesRegex(ValueError, 'total'):
            snake.validate_calendar(bad)

    def test_calendar_api_errors_are_not_empty_success(self):
        with patch.object(snake.subprocess, 'run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'API request failed'):
                snake.fetch_calendar()
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({'errors': [{'message': 'partial'}]})
            with self.assertRaisesRegex(ValueError, 'partial data'):
                snake.fetch_calendar()

    def test_both_svg_themes_encode_real_growth_food_and_loop_reset(self):
        models = []
        for dark in (False, True):
            root = ET.fromstring(snake.render_svg(calendar(), dark=dark))
            model = json.loads(root.find('{*}metadata').text)
            models.append(model)
            self.assertEqual(model['frames'][0]['length'], 3)
            self.assertEqual(model['frames'][-1]['length'], 77)
            self.assertEqual(model['frames'][-1]['eaten'], 74)
            self.assertLess(len(model['frames']) - 1, 53 * 7)
            self.assertLessEqual(model['durationSeconds'], 12)
            meals = {p['eatStep'] for p in model['food']}
            for i, (a, b) in enumerate(zip(model['frames'], model['frames'][1:]), 1):
                self.assertEqual(b['length'] - a['length'], int(i in meals))
            segments = root.findall('.//{*}rect[@class="segment"]')
            self.assertEqual(sum(s.attrib['opacity'] == '1' for s in segments), 3)
            day_animations = root.findall('.//{*}rect[@class="day"]/{*}animate')
            self.assertEqual(len(day_animations), 74)
            for animation in root.findall('.//{*}animate') + root.findall('.//{*}animateTransform'):
                self.assertEqual(animation.attrib['repeatCount'], 'indefinite')
                self.assertEqual(float(animation.attrib['dur'].removesuffix('s')),
                                 model['durationSeconds'])
                times = [float(k) for k in animation.attrib['keyTimes'].split(';')]
                self.assertEqual(times[0], 0)
                self.assertEqual(times[-1], 1)
                self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
                self.assertEqual(len(times), len(animation.attrib['values'].split(';')))
            for day in day_animations:
                values = day.attrib['values'].split(';')
                self.assertNotEqual(values[0], values[1])
                self.assertEqual(values[1], values[-1])
            for identity in ('head', 'tail'):
                motion = root.find(f'.//{{*}}g[@id="{identity}"]/{{*}}animateTransform')
                last_move = float(motion.attrib['keyTimes'].split(';')[-2])
                self.assertAlmostEqual((1 - last_move) * model['durationSeconds'],
                                       model['holdSeconds'], places=6)
                expected = [snake.pixel(f['head']) for f in model['frames']] if identity == 'head' else None
                if expected:
                    actual = [tuple(map(int, v.split())) for v in motion.attrib['values'].split(';')]
                    self.assertEqual(actual[:-1], expected)
                    self.assertEqual(actual[-1], actual[-2])
        self.assertEqual(models[0], models[1])

    def test_empty_calendar_has_no_growth_and_still_animates(self):
        root = ET.fromstring(snake.render_svg(calendar(active=False)))
        model = json.loads(root.find('{*}metadata').text)
        self.assertEqual(len(model['frames']) - 1, 6)
        self.assertTrue(all(f['length'] == 3 and f['eaten'] == 0 for f in model['frames']))
        self.assertEqual(root.findall('.//{*}rect[@class="day"]/{*}animate'), [])


if __name__ == '__main__':
    unittest.main()
