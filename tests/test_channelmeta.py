#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""频道标识归一化的回归测试

这些用例都是从真实踩过的坑里抽出来的 —— 每一条对应一次
「一个台显示成多个台」或「多个台塌成一个台」的事故。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import channelmeta as cm  # noqa: E402


class TestIdFromName(unittest.TestCase):
    def test_cctv_basic(self):
        self.assertEqual(cm.id_from_name('CCTV-1'), 'cctv1')
        self.assertEqual(cm.id_from_name('CCTV-1 综合'), 'cctv1')
        self.assertEqual(cm.id_from_name('CCTV 5+ 体育赛事'), 'cctv5p')
        self.assertEqual(cm.id_from_name('CCTV5PLUS'), 'cctv5p')

    def test_4k_is_not_cctv4(self):
        # CCTV-4K / 8K 是独立频道, 绝不能并进 CCTV-4
        self.assertEqual(cm.id_from_name('CCTV-4K'), 'cctv4k')
        self.assertEqual(cm.id_from_name('CCTV-8K'), 'cctv8k')
        self.assertEqual(cm.id_from_name('CCTV-4 中文国际'), 'cctv4')

    def test_weishi_prefix_must_not_swallow_local(self):
        # 浙江新闻 / 钱江都市 曾被 startswith('浙江') 全吞进浙江卫视
        self.assertEqual(cm.id_from_name('浙江卫视'), 'zjws')
        for nm in ('浙江新闻', '浙江民生休闲', '钱江都市', '浙江少儿'):
            self.assertNotEqual(cm.id_from_name(nm), 'zjws', nm)

    def test_cgtn_family_split(self):
        self.assertEqual(cm.id_from_name('CGTN'), 'cgtn')
        self.assertNotEqual(cm.id_from_name('CGTN纪录'), 'cgtn')
        # 记录 / 纪录 两种写法必须归一
        self.assertEqual(cm.id_from_name('CGTN记录'), cm.id_from_name('CGTN纪录'))

    def test_english_names(self):
        self.assertEqual(cm.id_from_name('Hunan TV (2160p)'), 'hnws')
        self.assertEqual(cm.id_from_name('Zhejiang TV'), 'zjws')


class TestStripQuality(unittest.TestCase):
    def test_md_hd(self):
        self.assertEqual(cm.strip_quality('cctv1hd'), 'cctv1')
        self.assertEqual(cm.strip_quality('cctv1md'), 'cctv1')
        self.assertEqual(cm.strip_quality('zjwshd'), 'zjws')
        self.assertEqual(cm.strip_quality('zjwsmd'), 'zjws')

    def test_4k_kept(self):
        self.assertEqual(cm.strip_quality('cctv4k'), 'cctv4k')
        self.assertEqual(cm.strip_quality('cctv8k'), 'cctv8k')


class TestProvince(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cm.load_prov_map(os.path.join(ROOT, 'data', 'prov_map.json'))

    def test_direct_province_prefix(self):
        self.assertEqual(cm.prov_of('山东新闻综合'), '山东')
        self.assertEqual(cm.prov_of('北京文艺'), '北京')

    def test_city_prefix(self):
        # 潍坊/东丰/乐至 这些县级台靠 prov_map 兜底
        self.assertEqual(cm.prov_of('潍坊新闻综合'), '山东')
        self.assertEqual(cm.prov_of('东丰新闻'), '吉林')
        self.assertEqual(cm.prov_of('乐至综合'), '四川')

    def test_vod_group(self):
        self.assertEqual(cm.prov_of('电影丧尸片'), cm.VOD_GROUP)
        self.assertEqual(cm.prov_of('八点剧场'), cm.VOD_GROUP)

    def test_unknown_falls_to_other(self):
        self.assertEqual(cm.prov_of('某某不存在的台'), cm.OTHER_GROUP)


class TestNameKey(unittest.TestCase):
    def test_generic_suffix(self):
        self.assertEqual(cm.name_key('乐至综合'), cm.name_key('乐至综合频道'))
        self.assertEqual(cm.name_key('邢台综合'), cm.name_key('邢台综合频道'))

    def test_suffix_does_not_merge_real_channels(self):
        self.assertNotEqual(cm.name_key('CGTN纪录'), cm.name_key('CGTN英语'))


if __name__ == '__main__':
    unittest.main()
