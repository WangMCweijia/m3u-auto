# -*- coding: utf-8 -*-
"""gslb 轮盘门禁的回归测试。

这道门要挡住的核心风险: 一条 URL 单轮撞上真源、多轮才露出广告池 ——
如果门禁只看一轮, 订阅里就会重新混进广告。
"""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import gslbgate


GW = 'http://38.75.136.137:98/gslb/dsdqbv/cctv6hd.m3u8?auth=test20251009'
REAL = 'http://142.54.162.26:82/live/cctv6md.m3u8?jsbt=1&jsbk=abc'
REAL2 = 'http://204.12.241.18:82/live/cctv6md.m3u8?jsbt=2&jsbk=def'
AD = 'http://47.97.252.137:81/applive/yss.m3u8?app=zjstream3'


def fake_probe(seq):
    """按顺序返回 (ok, detail, quality) 并写入 ctx['final']"""
    it = iter(seq)

    def _p(url, headers=None, timeout=8, depth=0, quality=(0, 0), ctx=None):
        ok, final, q = next(it)
        if ctx is not None:
            ctx['final'] = final
        return ok, 'id', (q or (1080, 0))
    return _p


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self._orig_probe = gslbgate.hlslive.probe_live
        self._orig_sleep = gslbgate.time.sleep
        gslbgate.time.sleep = lambda *_: None

    def tearDown(self):
        gslbgate.hlslive.probe_live = self._orig_probe
        gslbgate.time.sleep = self._orig_sleep

    def test_all_rounds_same_real_stream_passes(self):
        gslbgate.hlslive.probe_live = fake_probe(
            [(True, REAL, (1080, 0))] * 4)
        r = gslbgate.judge(GW, rounds=4)
        self.assertTrue(r['ok'])
        self.assertEqual(r['kind'], 'ok')
        self.assertEqual(r['key'], 'live/cctv6md')

    def test_cross_ip_rotation_still_same_stream(self):
        # 边缘 IP 轮换属于"同一个流", 不算指向不稳
        gslbgate.hlslive.probe_live = fake_probe(
            [(True, REAL, (1080, 0)), (True, REAL2, (1080, 0))] * 2)
        r = gslbgate.judge(GW, rounds=4)
        self.assertTrue(r['ok'], r['why'])

    def test_one_ad_round_kills_it(self):
        # 单轮撞真源、后面露出广告池 —— 正是漏判场景
        gslbgate.hlslive.probe_live = fake_probe([
            (True, REAL, (1080, 0)),
            (True, AD, (1080, 0)),
            (True, REAL, (1080, 0)),
            (True, REAL, (1080, 0)),
        ])
        r = gslbgate.judge(GW, rounds=4)
        self.assertFalse(r['ok'])
        self.assertEqual(r['kind'], 'ad')
        self.assertIn('applive/yss', r['why'])

    def test_unstable_targets_rejected(self):
        gslbgate.hlslive.probe_live = fake_probe([
            (True, 'http://a.com/live/cctv1.m3u8', (1080, 0)),
            (True, 'http://b.com/live/cctv5.m3u8', (1080, 0)),
            (True, 'http://a.com/live/cctv1.m3u8', (1080, 0)),
            (True, 'http://a.com/live/cctv1.m3u8', (1080, 0)),
        ])
        r = gslbgate.judge(GW, rounds=4)
        self.assertFalse(r['ok'])
        self.assertEqual(r['kind'], 'unstable')

    def test_dead_first_round_short_circuits(self):
        calls = []

        def _p(url, headers=None, timeout=8, depth=0, quality=(0, 0), ctx=None):
            calls.append(url)
            return False, 'not-m3u8:text/html', (0, 0)
        gslbgate.hlslive.probe_live = _p
        r = gslbgate.judge(GW, rounds=4)
        self.assertFalse(r['ok'])
        self.assertEqual(r['kind'], 'dead')
        self.assertEqual(len(calls), 1)      # 死链不重试, 省请求


class GateTest(unittest.TestCase):
    def setUp(self):
        self._orig_probe = gslbgate.hlslive.probe_live
        self._orig_sleep = gslbgate.time.sleep
        gslbgate.time.sleep = lambda *_: None
        fd, self.store = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        os.unlink(self.store)

    def tearDown(self):
        gslbgate.hlslive.probe_live = self._orig_probe
        gslbgate.time.sleep = self._orig_sleep
        if os.path.exists(self.store):
            os.unlink(self.store)

    def test_filters_items_and_sets_final(self):
        good = {'name': 'CCTV-6 电影', 'tvg_id': 'CCTV6', 'url': GW + '&x=1'}
        bad = {'name': 'CCTV-1 综合', 'tvg_id': 'CCTV1',
               'url': 'http://38.75.136.137:98/gslb/dsdqbv/cctv1hd.m3u8?auth=z'}
        direct = {'name': 'CCTV-2 财经', 'tvg_id': 'CCTV2',
                  'url': 'http://198.204.228.26/live/cctv2hd.m3u8'}

        def _p(url, headers=None, timeout=8, depth=0, quality=(0, 0), ctx=None):
            if 'cctv1hd' in url:
                if ctx is not None:
                    ctx['final'] = AD
                return True, 'id', (1080, 0)
            if ctx is not None:
                ctx['final'] = REAL
            return True, 'id', (1080, 0)
        gslbgate.hlslive.probe_live = _p

        items = [good, bad, direct]
        passed = gslbgate.gate(items, path=self.store, rounds=2, verbose=False)
        self.assertEqual(passed, {good['url']})
        self.assertEqual([i['name'] for i in items], ['CCTV-6 电影', 'CCTV-2 财经'])
        self.assertEqual(good['final'], REAL)          # sharedstream 要用
        self.assertEqual(good['gate_key'], 'live/cctv6md')

    def test_explicit_path_does_not_fall_back_to_repo_store(self):
        """显式路径不存在时必须当成空表 —— 否则会读到仓库里真实的 deny 表"""
        self.assertFalse(os.path.exists(self.store))
        d = gslbgate.load(self.store)
        self.assertEqual(d, {'pass': {}, 'deny': {}})

    def test_deny_survives_next_build(self):
        calls = []

        def _p(url, headers=None, timeout=8, depth=0, quality=(0, 0), ctx=None):
            calls.append(url)
            if ctx is not None:
                ctx['final'] = AD
            return True, 'id', (1080, 0)
        gslbgate.hlslive.probe_live = _p

        items = [{'name': 'X', 'tvg_id': 'X', 'url': GW}]
        gslbgate.gate(items, path=self.store, rounds=2, verbose=False)
        n1 = len(calls)
        self.assertGreater(n1, 0)

        # 第二次构建: deny 命中, 一次请求都不该发
        calls.clear()
        items2 = [{'name': 'X', 'tvg_id': 'X', 'url': GW}]
        gslbgate.gate(items2, path=self.store, rounds=2, verbose=False)
        self.assertEqual(calls, [])
        self.assertEqual(items2, [])

    def test_pass_reused_within_a_day(self):
        calls = []

        def _p(url, headers=None, timeout=8, depth=0, quality=(0, 0), ctx=None):
            calls.append(url)
            if ctx is not None:
                ctx['final'] = REAL
            return True, 'id', (1080, 0)
        gslbgate.hlslive.probe_live = _p

        gslbgate.gate([{'name': 'X', 'tvg_id': 'X', 'url': GW}],
                      path=self.store, rounds=2, verbose=False)
        n1 = len(calls)
        calls.clear()
        items = [{'name': 'X', 'tvg_id': 'X', 'url': GW}]
        passed = gslbgate.gate(items, path=self.store, rounds=2, verbose=False)
        self.assertEqual(calls, [])
        self.assertEqual(passed, {GW})
        self.assertEqual(len(items), 1)
        self.assertGreater(n1, 0)


if __name__ == '__main__':
    unittest.main()
