"""Layout checks on the bundled renderer, using fictional data only."""
import base64
import io
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageFont

from agent_console import daily_report as renderer

BASE = 1793077200


def avatar_uri():
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), (120, 90, 200)).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def sample(**changes):
    report = {
        'edition': 'review', 'chat_username': 'demo', 'chat_display': '星河读书会（虚构）', 'day': '2026-10-29',
        'range_text': '2026.10.28 08:00 — 2026.10.29 08:00', 'generated_at': '2026-10-29T08:01:00+08:00',
        'timezone': 'Asia/Shanghai', 'one_line': '今晚敲定了下月共读书目。',
        'stats': {'messages': 428, 'members': 37}, 'leaders': {'peak': {'name': '2026-10-28 20:40', 'count': 22}},
        'rhythm_buckets': [{'start_unix': BASE + 600 * i, 'end_unix_exclusive': BASE + 600 * (i + 1), 'count': i % 7} for i in range(144)],
        'rhythm_bucket_seconds': 600,
        'topics': [{'topic': '下月共读书目投票', 'summary': '《群星的回声》胜出。', 'details': ['每周两章', '周日讨论'],
                    'participant_ids': ['a', 'b'], 'start_unix': BASE, 'end_unix': BASE + 3600,
                    'references': [{'author': '林小满', 'author_id': 'a', 'timestamp': BASE, 'type': 'text', 'text': '投票结束'}]}],
        'quotes': [{'text': '好书就像好朋友。', 'speaker': '林小满', 'speaker_id': 'a', 'time': '10.28 21:47'}],
        'participants': [{'username': 'a', 'name': '林小满', 'tokens': 90}, {'username': 'b', 'name': '周禾', 'tokens': 40}],
        'avatars': {'a': {'available': True, 'data_uri': avatar_uri()}},
    }
    report.update(changes)
    return report


def draw(report):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'out.png'
        renderer.render_png_pillow(report, path)
        with Image.open(path) as image:
            image.load()
            return image.copy()


def page(report):
    with patch.object(renderer, 'rounded_webfont_css', return_value=''):
        return renderer.render_html(report)


class Structure(HTMLParser):
    """Records the elements the site build rewrites, and fails on stray end tags."""
    VOID = {'meta', 'img', 'line', 'circle', 'polyline', 'br'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.seen, self.errors = [], [], []
        self.text = ''

    def handle_starttag(self, tag, attrs):
        self.seen.append((tag, dict(attrs).get('class', ''), dict(attrs).get('id', '')))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.seen.append((tag, dict(attrs).get('class', ''), dict(attrs).get('id', '')))

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)

    def handle_data(self, data):
        if not self.stack or self.stack[-1] not in ('style', 'title'):
            self.text += data


def parsed(report):
    reader = Structure()
    reader.feed(page(report))
    return reader


class PngLayoutTests(unittest.TestCase):
    def test_png_is_phone_width_and_much_shorter_than_a_long_scroll(self):
        image = draw(sample())
        self.assertEqual(image.width, renderer.PNG_WIDTH)
        self.assertLess(image.height, 2400)

    def test_review_is_taller_than_brief_only_through_topic_detail(self):
        self.assertGreater(draw(sample()).height, draw(sample(edition='brief')).height)

    def test_height_grows_with_content_and_never_clips(self):
        short, long = draw(sample()), draw(sample(one_line='很长的总结。' * 60))
        self.assertGreater(long.height, short.height + 400)
        bottom = long.crop((0, long.height - 40, long.width, long.height)).convert('L')
        self.assertEqual(bottom.getextrema(), (255, 255))

    def test_edge_cases_still_render(self):
        name = '超级无敌长的群友昵称' * 12
        cases = {
            'long nickname': sample(participants=[{'username': 'a', 'name': name, 'tokens': 5}],
                                    quotes=[{'text': '短句', 'speaker': name, 'time': '10:00'}]),
            'long chat name': sample(chat_display=name, title_prefix='一个特别特别特别长的日报标题前缀' * 3),
            'no avatars': sample(avatars={}),
            'broken avatar': sample(avatars={'a': {'available': True, 'data_uri': 'data:image/png;base64,AAAA'}}),
            'very long quote': sample(quotes=[{'text': '这是一句超长的金句，' * 80, 'speaker': '林小满'}]),
            'emoji and unknown glyphs': sample(one_line='今天好开心🎉🍊', quotes=[{'text': '🙂', 'speaker': '🎈'}]),
            'long latin word': sample(one_line='https://example.org/' + 'a' * 300),
            'no images or sources': sample(topics=[{'topic': '只有标题'}]),
            'empty report': {'edition': 'brief'},
            'no rhythm': sample(rhythm_buckets=[]),
            'single bucket': sample(rhythm_buckets=[{'start_unix': BASE, 'count': 3}]),
            'missing stats': sample(stats={}, leaders={}),
            'long peak text': sample(leaders={'peak': {'name': '一段很长的高峰时段文字描述', 'count': 2}}),
        }
        for label, report in cases.items():
            with self.subTest(label):
                image = draw(report)
                self.assertEqual(image.width, renderer.PNG_WIDTH)
                self.assertGreater(image.height, 200)

    def test_wrapping_keeps_words_and_punctuation_rules(self):
        font = ImageFont.truetype(str(renderer.font_path()), 40)
        lines = renderer._wrap_text('这是一段用于测试中文换行规则的文字，标点不应该出现在行首。（括号）也一样！' * 3, font, 400)
        self.assertGreater(len(lines), 3)
        self.assertFalse([line for line in lines if line[0] in renderer._NO_LINE_START])
        self.assertFalse([line for line in lines if line[-1] in renderer._NO_LINE_END])
        self.assertIn('supercalifragilistic', renderer._wrap_text('测试 supercalifragilistic 单词', font, 800)[0])
        self.assertTrue(all(font.getlength(line) <= 400 for line in renderer._wrap_text('a' * 200, font, 400)))

    def test_avatars_from_the_report_data_are_painted_and_missing_ones_fall_back(self):
        buffer = io.BytesIO()
        Image.new('RGB', (64, 64), (255, 0, 0)).save(buffer, 'PNG')
        uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        people = [{'username': 'a', 'name': '林小满', 'tokens': 90}]
        def red_pixels(avatars):
            image = draw(sample(participants=people, active_rank=people, avatars=avatars)).convert('RGB')
            return sum(count for count, colour in image.getcolors(1 << 24) if colour[0] > 200 and colour[1] < 60 and colour[2] < 60)
        self.assertGreater(red_pixels({'a': {'available': True, 'source': 'export', 'data_uri': uri}}), 500)
        self.assertEqual(red_pixels({'a': {'available': False, 'data_uri': '', 'source': '', 'reason': 'avatar_not_exported'}}), 0)
        self.assertEqual(red_pixels({}), 0)

    def test_emoji_names_render_with_fallback_font_instead_of_becoming_qunyou(self):
        drawn = []
        original = renderer._PngPage.write
        def write(page_, x, baseline, text, font, fill, anchor='ls'):
            drawn.append(str(text))
            return original(page_, x, baseline, text, font, fill, anchor)
        emoji = '🍊'
        mixed = '🍍㊗️'
        report = sample(chat_display=emoji, participants=[
            {'username': 'a', 'name': emoji, 'tokens': 9},
            {'username': 'b', 'name': mixed, 'tokens': 5},
        ], quotes=[{'text': '短句🎉', 'speaker': emoji, 'time': '10:00'}],
            topics=[{'topic': emoji, 'summary': '概述'}])
        with patch.object(renderer._PngPage, 'write', write):
            draw(report)
        self.assertIn('🍊', drawn, 'pure emoji rank row')
        self.assertIn('🍍㊗', drawn, 'mixed emoji + CJK rank row')
        self.assertIn('🍊 · 10:00', drawn, 'pure emoji quote speaker')
        self.assertNotIn('群友', drawn)

    def test_names_the_font_cannot_draw_do_not_leave_a_blank(self):
        drawn = []
        original = renderer._PngPage.write
        def write(page_, x, baseline, text, font, fill, anchor='ls'):
            drawn.append(str(text))
            return original(page_, x, baseline, text, font, fill, anchor)
        unassigned = '\uE000\uE001'
        report = sample(chat_display=unassigned, participants=[{'username': 'a', 'name': unassigned, 'tokens': 5}],
                        quotes=[{'text': '短句', 'speaker': unassigned, 'time': '10:00'}],
                        topics=[{'topic': unassigned, 'summary': '概述'}])
        with patch.object(renderer._PngPage, 'write', write):
            draw(report)
        self.assertIn('群友', drawn, 'rank row')
        self.assertIn('群友 · 10:00', drawn, 'quote line')
        self.assertIn('聊天日报', drawn)
        self.assertIn('未命名话题', drawn)
        self.assertFalse([text for text in drawn if text.startswith(' ·')])
    def test_runs_of_closing_punctuation_stay_inside_the_margin(self):
        size = 40
        font = ImageFont.truetype(str(renderer.font_path()), size)
        width = renderer.PNG_WIDTH - 2 * renderer.PNG_MARGIN
        cases = ['这' * 20 + '—' * 40, '嗯' * 21 + '…' * 30, '哈' * 21 + '？' * 30, '好' * 23 + '。' * 30, '！' * 80, 'a b ' * 10 + '!' * 200]
        for text in cases:
            with self.subTest(text[:3]):
                lines = renderer._wrap_text(text, font, width)
                self.assertEqual(''.join(lines).replace(' ', ''), text.replace(' ', ''), 'no character may be lost')
                self.assertLessEqual(max(font.getlength(line) for line in lines), width + size)
                self.assertLess(width + size, width + renderer.PNG_MARGIN, 'one hanging character stays on the canvas')

    def test_one_hanging_closing_mark_keeps_the_line_rule(self):
        font = ImageFont.truetype(str(renderer.font_path()), 40)
        lines = renderer._wrap_text('好' * 10 + '。后面还有文字', font, 400)
        self.assertEqual(lines[0], '好' * 10 + '。')

    def test_missing_glyphs_are_dropped_but_spaces_and_emoji_survive(self):
        path = renderer.font_path()
        self.assertEqual(renderer._printable('开心🎉\uE000　快乐', path), '开心🎉　快乐')
        self.assertEqual(renderer._printable('开心🎉　快乐', path, None), '开心　快乐')

class HtmlTests(unittest.TestCase):
    def test_markup_is_balanced_and_keeps_the_hooks_the_site_build_rewrites(self):
        reader = parsed(sample())
        self.assertEqual((reader.errors, reader.stack), ([], []))
        names = {(tag, cls) for tag, cls, _ in reader.seen}
        for hook in [('div', 'chart'), ('ol', 'rank'), ('ul', 'sources'), ('p', 'topic-time'), ('blockquote', ''), ('p', 'body-chinese-count muted')]:
            self.assertIn(hook, names)
        self.assertIn(('article', 'topic', 'topic-1'), reader.seen)
        source = page(sample())
        self.assertRegex(source, r'<small>高峰时段</small><b[^>]*>10-28 20:40</b>')
        self.assertIn('href="#topic-1"', source)

    def test_chat_text_is_escaped_everywhere(self):
        attack = '<img src=x onerror=alert(1)>'
        source = page(sample(chat_display=attack, one_line=attack, title_prefix=attack, range_text=attack,
                             topics=[{'topic': attack, 'summary': attack, 'details': [attack]}],
                             quotes=[{'text': attack, 'speaker': attack, 'time': attack}],
                             participants=[{'username': 'x', 'name': attack, 'tokens': 1}]))
        self.assertNotIn('<img src=x', source)

    def test_page_needs_no_network_and_declares_a_policy(self):
        source = page(sample())
        self.assertIn('Content-Security-Policy', source)
        self.assertNotRegex(source, r'(?:src|href)=["\']https?:')
        self.assertNotIn('<script', source)

    def test_edge_cases_render(self):
        for report in ({'edition': 'brief'}, sample(topics=[], quotes=[], participants=[], rhythm_buckets=[], avatars={})):
            reader = parsed(report)
            self.assertEqual((reader.errors, reader.stack), ([], []))

    def test_title_comes_from_data_then_configuration_then_a_neutral_default(self):
        self.assertIn('群聊日报 · 回顾', page({}))
        self.assertIn('QQ群总结 · 回顾', page({'title_prefix': 'QQ群总结'}))
        with patch.dict('os.environ', {'CHAT_DIGEST_REPORT_TITLE': '星河周报'}):
            self.assertIn('星河周报 · 回顾', page({}))
            self.assertIn('QQ群总结 · 回顾', page({'title_prefix': 'QQ群总结'}))

    def test_generated_time_is_friendly_and_unparsable_text_stays_as_written(self):
        self.assertIn('生成于 2026.10.29 08:01', parsed(sample()).text)
        self.assertIn('合成样例', parsed(sample(generated_at='合成样例')).text)

    def test_topic_people_appear_only_in_the_review(self):
        self.assertIn('class="people"', page(sample()))
        self.assertNotIn('class="people"', page(sample(edition='brief')))


if __name__ == '__main__':
    unittest.main()
