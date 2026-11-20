#!/usr/bin/env python3
"""Render a growing contribution snake using only Python's standard library.

The column-wise serpentine route visits each calendar cell once. The full body
follows its head's history: eating keeps the old tail, empty moves remove it.
SVG animations share one clock and reset only at the end of the whole cycle.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


USER = 'boyfeiyu'
INITIAL_LENGTH = 3
STEP_SECONDS = 0.12
HOLD_STEPS = 18
PITCH = 16
LEVELS = ('NONE', 'FIRST_QUARTILE', 'SECOND_QUARTILE', 'THIRD_QUARTILE', 'FOURTH_QUARTILE')
QUERY = '''query($login: String!) {
  user(login: $login) {
    login
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount contributionLevel } }
      }
    }
  }
}'''


def validate_calendar(calendar):
    if calendar['username'] != USER:
        raise ValueError('Unexpected calendar user.')
    datetime.strptime(calendar['fetchedAt'], '%Y-%m-%dT%H:%M:%SZ')
    weeks = calendar['weeks']
    if not 52 <= len(weeks) <= 54:
        raise ValueError('Incomplete contribution calendar weeks.')
    dates = []
    total = 0
    for x, week in enumerate(weeks):
        days = week['contributionDays']
        if not days or len(days) > 7:
            raise ValueError('Invalid calendar week.')
        for day in days:
            d = date.fromisoformat(day['date'])
            weekday = day['weekday']
            count = day['contributionCount']
            if type(weekday) is not int or weekday != (d.weekday() + 1) % 7:
                raise ValueError('Calendar weekday disagrees with date.')
            if type(count) is not int or count < 0 or day['contributionLevel'] not in LEVELS:
                raise ValueError('Invalid contribution count or level.')
            if (count == 0) != (day['contributionLevel'] == 'NONE'):
                raise ValueError('Contribution count and color disagree.')
            if dates and (d - dates[0]).days != x * 7 + weekday - first_weekday:
                raise ValueError('Calendar cell is in the wrong week.')
            if not dates:
                first_weekday = weekday
            dates.append(d)
            total += count
    # GitHub pads the first partial week back to Sunday (up to six extra days).
    if not 365 <= len(dates) <= 372 or len(set(dates)) != len(dates):
        raise ValueError('Incomplete or duplicate calendar days.')
    if any((b - a).days != 1 for a, b in zip(dates, dates[1:])):
        raise ValueError('Calendar dates are not consecutive.')
    if total != calendar['totalContributions']:
        raise ValueError('Incomplete calendar contribution total.')


def fetch_calendar():
    result = subprocess.run(['gh', 'api', 'graphql', '--input', '-'],
                            input=json.dumps({'query': QUERY, 'variables': {'login': USER}}),
                            text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise ValueError('Calendar API request failed; existing snake files left unchanged.')
    response = json.loads(result.stdout)
    if response.get('errors'):
        raise ValueError('Calendar API returned partial data; refusing to publish.')
    user = response['data']['user']
    calendar = {
        'username': user['login'],
        'fetchedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        **user['contributionsCollection']['contributionCalendar'],
    }
    validate_calendar(calendar)
    return calendar


@dataclass(frozen=True)
class Frame:
    body: tuple
    eaten: frozenset


def route_for_columns(columns):
    return [(x, y) for x in range(columns)
            for y in (range(7) if x % 2 == 0 else range(6, -1, -1))]


def simulate(food, route, initial_length=INITIAL_LENGTH):
    if type(initial_length) is not int or initial_length < 1:
        raise ValueError('Initial snake length must be positive.')
    frames = [Frame(tuple((-i - 1, 0) for i in range(initial_length)), frozenset())]
    for point in route:
        previous = frames[-1]
        if sum(abs(a - b) for a, b in zip(point, previous.body[0])) != 1:
            raise ValueError('Snake must move to an adjacent cell.')
        grows = point in food and point not in previous.eaten
        if point in (previous.body if grows else previous.body[:-1]):
            raise ValueError('Snake would collide with its body.')
        body = (point,) + previous.body
        if not grows:
            body = body[:-1]
        eaten = previous.eaten | {point} if grows else previous.eaten
        frames.append(Frame(body, frozenset(eaten)))
    return frames


def pixel(point):
    return 64 + point[0] * PITCH, 48 + point[1] * PITCH


def opacity_animation(first, last, total_steps, duration):
    """One occupied interval; repeat restores each cell's initial occupancy."""
    keys, values = [0.0], ['1' if first == 0 else '0']
    if first:
        keys.append(first / total_steps)
        values.append('1')
    if last + 1 < total_steps:
        keys.append((last + 1) / total_steps)
        values.append('0')
    keys.append(1.0)
    values.append(values[-1])
    return (f'<animate attributeName="opacity" dur="{duration}s" repeatCount="indefinite" '
            f'calcMode="discrete" keyTimes="{";".join(f"{k:.8f}" for k in keys)}" '
            f'values="{";".join(values)}"/>')


def render_svg(calendar, dark=False):
    validate_calendar(calendar)
    food = {(x, day['weekday']): day for x, week in enumerate(calendar['weeks'])
            for day in week['contributionDays'] if day['contributionCount'] > 0}
    route = route_for_columns(len(calendar['weeks']))
    frames = simulate(food, route)
    total_steps = len(route) + HOLD_STEPS
    duration = round(total_steps * STEP_SECONDS, 2)
    times = ';'.join([f'{i/total_steps:.8f}' for i in range(len(frames))] + ['1'])
    meal_steps = {point: i for i, point in enumerate(route, 1) if point in food}
    model = {
        'schemaVersion': 1, 'username': USER, 'fetchedAt': calendar['fetchedAt'],
        'initialLength': INITIAL_LENGTH, 'initialBody': frames[0].body,
        'stepSeconds': STEP_SECONDS, 'holdSteps': HOLD_STEPS, 'totalSteps': total_steps,
        'food': [{'point': p, 'date': d['date'], 'count': d['contributionCount'],
                  'eatStep': meal_steps[p]} for p, d in food.items()],
        'frames': [{'head': f.body[0], 'length': len(f.body), 'eaten': len(f.eaten)} for f in frames],
    }
    colors = (['#161b22', '#0e4429', '#006d32', '#26a641', '#39d353'] if dark else
              ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39'])
    background, foreground = ('#0d1117', '#c9d1d9') if dark else ('#ffffff', '#57606a')
    body_color, head_color = ('#b69cff', '#d2a8ff') if dark else ('#8250df', '#5e2ca5')
    eye_color = background if dark else '#ffffff'
    tail_color = '#f2cc60' if dark else '#bf8700'
    width = (len(calendar['weeks']) - 1) * PITCH + 96
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="200" '
           f'viewBox="0 0 {width} 200" role="img" aria-labelledby="title desc">',
           '<title id="title">Growing contribution snake for boyfeiyu</title>',
           f'<desc id="desc">Starts with {INITIAL_LENGTH} segments. Each first visit to a green day '
           f'eats that day and adds one segment. Empty or previously eaten cells do not grow the snake. '
           f'Ends with {len(frames[-1].body)} segments, then resets the calendar and snake together. '
           'The darker head has eyes; the gold dot marks the tail.</desc>',
           f'<metadata id="snake-model">{escape(json.dumps(model, separators=(",", ":")))}</metadata>',
           f'<rect width="{width}" height="200" rx="12" fill="{background}"/>',
           f'<text x="16" y="21" fill="{foreground}" font-size="12" '
           'font-family="ui-monospace,monospace">boyfeiyu / contribution snake</text>']
    for x, week in enumerate(calendar['weeks']):
        for day in week['contributionDays']:
            point = (x, day['weekday'])
            px, py = pixel(point)
            fill = colors[LEVELS.index(day['contributionLevel'])]
            out.append(f'<rect class="day" data-point="{x},{point[1]}" data-count="{day["contributionCount"]}" '
                       f'x="{px-6}" y="{py-6}" width="12" height="12" rx="2" fill="{fill}">'
                       f'<title>{day["date"]}: {day["contributionCount"]} contributions</title>')
            if point in food:
                t = meal_steps[point] / total_steps
                out.append(f'<animate attributeName="fill" dur="{duration}s" repeatCount="indefinite" '
                           f'calcMode="discrete" keyTimes="0;{t:.8f};1" '
                           f'values="{fill};{colors[0]};{colors[0]}"/>')
            out.append('</rect>')
    # The generated route is unique, so each coordinate has one occupancy interval.
    intervals = {}
    for i, frame in enumerate(frames):
        for point in frame.body:
            if point not in intervals:
                intervals[point] = [i, i]
            intervals[point][1] = i
    for point, (first, last) in intervals.items():
        px, py = pixel(point)
        # Final body remains visible throughout the hold before the loop reset.
        last = total_steps - 1 if last == len(route) else last
        out.append(f'<rect class="segment" data-point="{point[0]},{point[1]}" '
                   f'x="{px-5}" y="{py-5}" width="10" height="10" rx="2" '
                   f'fill="{body_color}" opacity="{1 if first == 0 else 0}">'
                   f'{opacity_animation(first, last, total_steps, duration)}</rect>')

    def moving_transform(points):
        values = ';'.join(f'{x} {y}' for x, y in [pixel(p) for p in points] + [pixel(points[-1])])
        return (f'<animateTransform attributeName="transform" type="translate" '
                f'dur="{duration}s" repeatCount="indefinite" calcMode="linear" '
                f'keyTimes="{times}" values="{values}"/>')

    tail = pixel(frames[0].body[-1])
    out.append(f'<g id="tail" transform="translate({tail[0]} {tail[1]})">'
               f'{moving_transform([f.body[-1] for f in frames])}'
               f'<circle r="3" fill="{tail_color}"/></g>')
    angles = []
    for i, f in enumerate(frames):
        next_head = frames[min(i + 1, len(frames) - 1)].body[0]
        dx, dy = next_head[0] - f.body[0][0], next_head[1] - f.body[0][1]
        angles.append(90 if dy > 0 else (-90 if dy < 0 else (180 if dx < 0 else 0)))
    head = pixel(frames[0].body[0])
    out.append(f'<g id="head" transform="translate({head[0]} {head[1]})">'
               f'{moving_transform([f.body[0] for f in frames])}<g>'
               f'<animateTransform attributeName="transform" type="rotate" dur="{duration}s" '
               f'repeatCount="indefinite" calcMode="discrete" keyTimes="{times}" '
               f'values="{";".join(str(a) for a in angles + [angles[-1]])}"/>'
               f'<rect x="-6" y="-6" width="12" height="12" rx="3" fill="{head_color}"/>'
               f'<circle cx="2" cy="-3" r="1.4" fill="{eye_color}"/>'
               f'<circle cx="2" cy="3" r="1.4" fill="{eye_color}"/></g></g>')
    out.append(f'<text x="16" y="184" fill="{foreground}" font-size="11" '
               f'font-family="ui-monospace,monospace">START {INITIAL_LENGTH} · '
               f'{len(food)} GREEN DAYS · FINAL {len(frames[-1].body)} · +1 PER EATEN GREEN DAY</text></svg>\n')
    svg = ''.join(out)
    ET.fromstring(svg)
    return svg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calendar', type=Path, help='Render a saved calendar without API access')
    parser.add_argument('--save-calendar', type=Path, help='Save the successfully fetched calendar')
    parser.add_argument('--output-dir', type=Path, default=Path('dist'))
    args = parser.parse_args()
    calendar = json.loads(args.calendar.read_text()) if args.calendar else fetch_calendar()
    # Build and validate both themes before replacing either published file.
    outputs = {f'github-contribution-grid-snake{"-dark" if dark else ""}.svg':
               render_svg(calendar, dark=dark) for dark in (False, True)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, svg in outputs.items():
        (args.output_dir / name).write_text(svg, encoding='utf-8')
    if args.save_calendar:
        args.save_calendar.write_text(json.dumps(calendar, indent=2) + '\n', encoding='utf-8')
    active = sum(d['contributionCount'] > 0 for w in calendar['weeks'] for d in w['contributionDays'])
    print(f'Generated both themes: initial {INITIAL_LENGTH}, {active} eaten green days, '
          f'final {INITIAL_LENGTH + active} segments at {calendar["fetchedAt"]}.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Snake refresh failed: {error}', file=sys.stderr)
        sys.exit(1)
