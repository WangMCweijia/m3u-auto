#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广告黑名单三级匹配的回归测试 (全部离线)"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import adfilter  # noqa: E402


BL = {
    'host': {'10.0.0.1:82': {'reason': 'host-ad'}},
    'url': {'http://x.test/a.m3u8': {'reason': 'url-ad'}},
    'channel': {'大湾区卫视': {'reason': 'channel-ad'}},
}


class AdFilterTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(BL, f, ensure_ascii=False)

    def tearDown(self):
        os.unlink(self.path)

    def _items(self):
        return [
            {'name': 'CCTV-1 综合', 'url': 'http://ok.test/1.m3u8'},
            {'name': 'CCTV-2 财经', 'url': 'http://x.test/a.m3u8'},
            {'name': '浙江卫视', 'url': 'http://10.0.0.1:82/live/zj.m3u8'},
            {'name': '大湾区卫视', 'url': 'http://ok.test/gd.m3u8'},
            {'name': '大湾区卫视', 'url': 'http://ok2.test/gd.m3u8'},
        ]

    def test_url_level(self):
        items = self._items()
        adfilter.apply(items, self.path, verbose=False)
        urls = [i['url'] for i in items]
        self.assertNotIn('http://x.test/a.m3u8', urls)

    def test_host_level_keeps_port(self):
        """host 匹配必须带端口, 否则会把同 IP 的正常端口一起误杀"""
        items = self._items()
        adfilter.apply(items, self.path, verbose=False)
        urls = [i['url'] for i in items]
        self.assertNotIn('http://10.0.0.1:82/live/zj.m3u8', urls)
        self.assertIn('http://10.0.0.1:98/live/zj.m3u8',
                      [u for u in urls] + ['http://10.0.0.1:98/live/zj.m3u8'])
        hit, _ = adfilter.should_block('http://10.0.0.1:98/live/zj.m3u8',
                                       BL['host'], BL['url'])
        self.assertFalse(hit)

    def test_channel_level_drops_all_lines(self):
        items = self._items()
        n = adfilter.apply(items, self.path, verbose=False)
        names = [i['name'] for i in items]
        self.assertEqual(names.count('大湾区卫视'), 0)
        self.assertEqual(n, 4)          # 1 url + 1 host + 2 channel

    def test_channel_does_not_touch_others(self):
        items = self._items()
        adfilter.apply(items, self.path, verbose=False)
        self.assertIn('CCTV-1 综合', [i['name'] for i in items])

    def test_missing_channel_key_is_fine(self):
        """老版本黑名单只有 host/url 两个键, 不能因为少 channel 就崩"""
        bl = {'host': {}, 'url': {}}
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(bl, f)
        host_map, url_map, ch_map = adfilter.load(self.path)
        self.assertEqual(ch_map, {})
        items = self._items()
        self.assertEqual(adfilter.apply(items, self.path, verbose=False), 0)
        self.assertEqual(len(items), 5)

    def test_empty_maps_block_nothing(self):
        hit, reason = adfilter.should_block('http://any.test/a.m3u8', {}, {}, '任何台')
        self.assertFalse(hit)
        self.assertEqual(reason, '')


if __name__ == '__main__':
    unittest.main()
