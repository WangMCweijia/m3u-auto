#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3U 读写 / 归并 / EPG 对齐的回归测试 (全部离线)"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import epg  # noqa: E402


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()
import m3uio as io_  # noqa: E402
import verifystate as vs  # noqa: E402

SAMPLE = '''#EXTM3U
#EXTINF:-1 tvg-id="cctv1hd" tvg-name="CCTV-1 综合" group-title="央视",CCTV-1 综合
http://a/1.m3u8
#EXTINF:-1 tvg-id="cctv1" tvg-name="CCTV-1 综合" group-title="央视",CCTV-1 综合
http://a/2.m3u8
#EXTINF:-1 tvg-id="1" tvg-name="电影丧尸片" group-title="电影",电影丧尸片
http://a/3.m3u8
#EXTINF:-1 tvg-id="zjws" tvg-name="浙江卫视" group-title="卫视",浙江卫视 [1080P]
#EXTVLCOPT:http-referrer=http://ref/
http://a/4.m3u8
'''


class TestParse(unittest.TestCase):
    def test_hd_and_plain_merge_into_one_channel(self):
        items = io_.parse_m3u(SAMPLE)
        cctv1 = [i for i in items if i['tvg_id'] == 'cctv1']
        self.assertEqual(len(cctv1), 2, 'cctv1hd 与 cctv1 必须归到同一个 id')

    def test_numeric_id_dropped(self):
        items = io_.parse_m3u(SAMPLE)
        self.assertFalse([i for i in items if i['tvg_id'].isdigit()],
                         '纯数字 tvg-id 必须丢弃, 否则一批台塌成一个')

    def test_quality_tag_stripped(self):
        items = io_.parse_m3u(SAMPLE)
        zj = [i for i in items if '浙江' in i['name']]
        self.assertTrue(zj)
        self.assertEqual(zj[0]['name'], '浙江卫视')
        self.assertEqual(zj[0]['group'], '卫视',
                         '带 [1080P] 时 endswith(卫视) 会失效')

    def test_extvlcopt_skipped(self):
        items = io_.parse_m3u(SAMPLE)
        zj = [i for i in items if '浙江' in i['name']][0]
        self.assertEqual(zj['url'], 'http://a/4.m3u8',
                         'EXTINF 与 URL 之间的 #EXTVLCOPT 不能被当成分片')


class TestWrite(unittest.TestCase):
    def _items(self):
        return [
            dict(tvg_id='cctv1', name='CCTV-1 综合', group='央视',
                 url='http://a/1', quality=(2160, 0), steady=3, order=0),
            dict(tvg_id='cctv1', name='CCTV-1 综合', group='央视',
                 url='http://a/2', quality=(720, 0), steady=0, order=1),
            dict(tvg_id='zjws', name='浙江卫视', group='卫视',
                 url='http://a/3', quality=(1080, 0),
                 headers={'Referer': 'http://ref/'}, order=2),
        ]

    def test_header_and_quality_order(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'x.m3u')
        io_.write_m3u(p, [dict(x) for x in self._items()])
        txt = _read(p)
        self.assertTrue(txt.startswith('#EXTM3U'))
        urls = [l for l in txt.splitlines() if l.startswith('http')]
        self.assertEqual(urls[0], 'http://a/1',
                         '4K 必须排在 720P 前面')

    def test_headers_emitted(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'x.m3u')
        io_.write_m3u(p, [dict(x) for x in self._items()])
        txt = _read(p)
        self.assertIn('#EXTVLCOPT:http-referrer=http://ref/', txt,
                      '验活时带了 Referer, 产物里丢了播放器就播不了')

    def test_epg_id_mapping(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'x.m3u')
        io_.write_m3u(p, [dict(x) for x in self._items()],
                      epg={'cctv1': 'CCTV1'}, epg_url='http://epg/e.xml')
        txt = _read(p)
        self.assertIn('x-tvg-url="http://epg/e.xml"', txt.splitlines()[0])
        self.assertIn('tvg-id="CCTV1"', txt)
        self.assertIn('tvg-id="zjws"', txt, '没有 EPG 映射的台保持原 id')

    def test_tvlist_quality_tag(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'x.txt')
        io_.write_tvlist(p, [dict(x) for x in self._items()])
        txt = _read(p)
        self.assertIn('[4K]', txt)
        self.assertIn('[1080P]', txt)


class TestEpgMap(unittest.TestCase):
    def test_rules(self):
        ids = {'CCTV1', 'CCTV5+', '浙江卫视', 'CGTN英语'}
        self.assertEqual(epg.map_one('cctv1', 'CCTV-1 综合', ids), 'CCTV1')
        self.assertEqual(epg.map_one('cctv5p', 'CCTV-5+ 体育赛事', ids), 'CCTV5+')
        self.assertEqual(epg.map_one('zjws', '浙江卫视', ids), '浙江卫视')
        self.assertEqual(epg.map_one('cgtndoc', 'CGTN英语', ids), 'CGTN英语')
        self.assertEqual(epg.map_one('sdws', '山东卫视', ids), '',
                         'EPG 没有的台不该硬塞 id')

    def test_missing_epg_is_fine(self):
        self.assertEqual(epg.map_one('cctv1', 'CCTV-1 综合', set()), '')


class TestVerifyState(unittest.TestCase):
    def test_streak_resets_on_failure(self):
        st = {}
        self.assertEqual(vs.mark(st, 'u1', True), 1)
        self.assertEqual(vs.mark(st, 'u1', True), 2)
        self.assertEqual(vs.mark(st, 'u1', False), 0)
        self.assertEqual(vs.steady(st, 'u1'), 0)


if __name__ == '__main__':
    unittest.main()
