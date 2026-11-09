# -*- coding: utf-8 -*-
"""广告池判据的回归测试。

核心风险有两个方向:
  - 漏判: 广告池照旧进订阅 (用户实际抱怨的问题)
  - 误杀: 把「同一个频道的多条码率」或「两三个频道共用一个源」当广告池
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import sharedstream


def mk(name, tvg_id, url, final=None):
    it = {'name': name, 'tvg_id': tvg_id, 'url': url}
    if final:
        it['final'] = final
    return it


class StreamKeyTest(unittest.TestCase):
    def test_strips_signature(self):
        self.assertEqual(
            sharedstream.stream_key(
                'http://204.12.234.154:88/applive/yss.m3u8?u=1&t=2&k=abc'),
            'applive/yss')

    def test_cross_ip_pool_collapses(self):
        """广告池会轮换边缘 IP, 同名的 applive/yss 必须归成一个"""
        a = sharedstream.stream_key('http://204.12.234.154:88/applive/yss.m3u8')
        b = sharedstream.stream_key('http://69.197.181.106:88/applive/yss.m3u8?t=9&k=z')
        c = sharedstream.stream_key('http://142.54.178.42:88/applive/yss.m3u8')
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_generic_name_keeps_host(self):
        """不同站点的 /live/index.m3u8 是两回事, 不能合并"""
        a = sharedstream.stream_key('http://a.com/live/index.m3u8')
        b = sharedstream.stream_key('http://b.com/live/index.m3u8')
        self.assertNotEqual(a, b)
        self.assertIn('a.com', a)

    def test_generic_dir_with_digit_keeps_host(self):
        """live/1 这类通用目录+数字最容易撞名"""
        a = sharedstream.stream_key('http://a.com/live/1.m3u8')
        b = sharedstream.stream_key('http://b.com/live/1.m3u8')
        self.assertNotEqual(a, b)

    def test_signature_variants_collapse(self):
        """同一个流的不同签名必须归成一个 key, 否则检测失效"""
        a = sharedstream.stream_key('http://h:88/applive/102.m3u8?t=1&k=a')
        b = sharedstream.stream_key('http://h:88/applive/102.m3u8?t=2&k=b')
        self.assertEqual(a, b)

    def test_strips_segment_index(self):
        # live 是通用目录, 剔掉后只剩一段, 退回 host+path
        self.assertEqual(sharedstream.stream_key('http://h/live/cctv6/segment_12420.ts'),
                         'h/live/cctv6')
        self.assertEqual(sharedstream.stream_key('http://h/live/cctv6/12420.ts'),
                         'h/live/cctv6')

    def test_county_channels_not_collapsed(self):
        """河北县级台是 .../live/<台代号>/1500k/tzwj_video.m3u8 ——
        码率段和通用文件名必须被剔掉, 否则 22 个县台会塌成一个"""
        a = sharedstream.stream_key(
            'http://jwcdnqx.hebyun.com.cn/live/rqtv1/1500k/tzwj_video.m3u8')
        b = sharedstream.stream_key(
            'http://jwcdnqx.hebyun.com.cn/live/NHTV1/1500k/tzwj_video.m3u8')
        self.assertNotEqual(a, b)
        self.assertIn('rqtv1', a)
        self.assertIn('NHTV1', b)

    def test_accepts_scheme_less(self):
        self.assertEqual(sharedstream.stream_key('h:88/applive/yss.m3u8'),
                         'applive/yss')

    def test_empty(self):
        self.assertIsNone(sharedstream.stream_key(''))
        self.assertIsNone(sharedstream.stream_key(None))


class ApplyTest(unittest.TestCase):
    def _pool(self, nchan, per=1, host='h:88', path='applive/yss'):
        items = []
        for c in range(nchan):
            for k in range(per):
                items.append(mk('CH%d' % c, 'ch%d' % c,
                                'http://gw%d/x.m3u8' % (c * 10 + k),
                                'http://%s/%s.m3u8?t=1&k=%d' % (host, path, c * 10 + k)))
        return items

    def test_pool_is_dropped(self):
        items = self._pool(5, per=2)   # 5 频道 / 10 条
        n = sharedstream.apply(items, verbose=False)
        self.assertEqual(n, 10)
        self.assertEqual(items, [])

    def test_below_channel_threshold_kept(self):
        items = self._pool(3)          # 3 频道, 未达 MIN_CHANNELS=4
        self.assertEqual(sharedstream.apply(items, verbose=False), 0)
        self.assertEqual(len(items), 3)

    def test_below_line_threshold_kept(self):
        """4 个频道但只有 4 条线路, 未达 MIN_LINES=6"""
        items = self._pool(4)
        self.assertEqual(sharedstream.apply(items, verbose=False), 0)
        self.assertEqual(len(items), 4)

    def test_same_channel_multi_bitrate_kept(self):
        """同一频道的 md/hd/4K 都落在一个 master 上, 不该算广告池"""
        items = []
        for k in range(8):
            items.append(mk('CCTV-1 综合', 'CCTV1', 'http://gw/%d.m3u8' % k,
                            'http://h:88/live/cctv1.m3u8'))
        self.assertEqual(sharedstream.apply(items, verbose=False), 0)
        self.assertEqual(len(items), 8)

    def test_mixed_pool_drops_only_pool(self):
        pool = self._pool(5, per=2)
        good = [mk('真台', 'good', 'http://x/live/cctv6md.m3u8',
                   'http://204.12.241.18:82/live/cctv6md.m3u8')]
        items = pool + good
        n = sharedstream.apply(items, verbose=False)
        self.assertEqual(n, 10)
        self.assertEqual([i['tvg_id'] for i in items], ['good'])

    def test_falls_back_to_url_without_final(self):
        items = [mk('CH%d' % c, 'ch%d' % c, 'http://h:88/applive/yss.m3u8')
                 for c in range(6)]
        n = sharedstream.apply(items, verbose=False)
        self.assertEqual(n, 6)

    def test_analyze_reports_counts(self):
        bad = sharedstream.analyze(self._pool(6))
        self.assertEqual(len(bad), 1)
        key, nch, nline, lst = bad[0]
        self.assertEqual(nch, 6)
        self.assertEqual(nline, 6)


if __name__ == '__main__':
    unittest.main()
