#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并公开源池 (iptv-org / fanmingming) 到已有订阅

用途: 91kds 表里央视与卫视几乎全走已停服的 kdsvod 协议, 存活率低,
      用公开池补全 CCTV/卫视。

用法:
  python merge_public.py --base out/live.m3u --out out/live.m3u
  python merge_public.py --base out/live.m3u --out out/live.m3u --no-probe
"""
import argparse
import concurrent.futures as cf
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

POOLS = [
    ('https://iptv-org.github.io/iptv/countries/cn.m3u', '央视/地方'),
    ('https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/m3u/ipv4.m3u', '央视/卫视'),
    ('https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/m3u/index.m3u', '央视/卫视'),
]

# 公开池里的英文名 -> 中文名
NAME_MAP = {
    'CCTV1': 'CCTV-1 综合',
    'CCTV2': 'CCTV-2 财经',
    'CCTV3': 'CCTV-3 综艺',
    'CCTV4': 'CCTV-4 中文国际',
    'CCTV5': 'CCTV-5 体育',
    'CCTV5PLUS': 'CCTV-5+ 体育赛事',
    'CCTV6': 'CCTV-6 电影',
    'CCTV7': 'CCTV-7 国防军事',
    'CCTV8': 'CCTV-8 电视剧',
    'CCTV9': 'CCTV-9 纪录',
    'CCTV10': 'CCTV-10 科教',
    'CCTV11': 'CCTV-11 戏曲',
    'CCTV12': 'CCTV-12 社会与法',
    'CCTV13': 'CCTV-13 新闻',
    'CCTV14': 'CCTV-14 少儿',
    'CCTV15': 'CCTV-15 音乐',
    'CCTV16': 'CCTV-16 奥林匹克',
    'CCTV17': 'CCTV-17 农业农村',
    'CCTV4K': 'CCTV-4K 超高清',
    'CCTV8K': 'CCTV-8K 超高清',
    'CCTVBILLIARDS': 'CCTV 台球',
    'CCTVGOLF&TENNIS': 'CCTV 高尔夫网球',
    'CCTVHEALTH': 'CCTV 卫生健康',
    'CCTVNOSTALGIATHEATER': 'CCTV 怀旧剧场',
    'CCTVSTORMFOOTBALL': 'CCTV 风云足球',
    'CCTVSTORMMUSIC': 'CCTV 风云音乐',
    'CCTVSTORMTHEATER': 'CCTV 风云剧场',
    'CCTVSANDSTORM': 'CCTV 风云剧场',
    'CCTVTEFIRSTTHEATER': 'CCTV 第一剧场',
    'CCTTTHEFIRSTTHEATER': 'CCTV 第一剧场',
    'CCTVWEAPON&TECHNOLOGY': 'CCTV 兵器科技',
    "CCTVWOMEN'SFASHION": 'CCTV 女性时尚',
    'CCTVWORLDGEOGRAPHY': 'CCTV 世界地理',
    'CCTVCULTUREOFQUALITY': 'CCTV 精品文化',
    'CCTVPLUS1': 'CCTV+ 1 对外',
    'CCTVPLUS2': 'CCTV+ 2 对外',
    'CCTVPLUS': 'CCTV+ 对外',
}

# 名称归一化时丢弃的修饰词
DROP_WORDS = re.compile(
    r'\b(HD|SD|FHD|UHD|HEVC|H\.?265|4K|8K|2160p|1080p|1080i|720p|576i|576p|480p)\b',
    re.I)


def norm_name(raw):
    n = re.sub(r'\s*\(\d{3,4}[pi]\)', '', raw)   # 去分辨率括号
    n = re.sub(r'\s*\[[^\]]*\]', '', n)           # 去 [Not 24/7] / [Geo-blocked]
    n = DROP_WORDS.sub('', n)
    n = re.sub(r'\s+', ' ', n).strip()
    key = n.replace(' ', '').replace('-', '').replace('_', '').upper()
    if key in NAME_MAP:
        return NAME_MAP[key]
    # 前缀匹配: CCTV-5 体育 / CCTV5Plus ...
    for k in sorted(NAME_MAP, key=len, reverse=True):
        if key.startswith(k):
            return NAME_MAP[k]
    return n


def parse_m3u(text):
    """返回 [(group, name, url, logo, extinf_raw)]"""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('#EXTINF'):
            g = re.search(r'group-title="([^"]*)"', line)
            group = g.group(1) if g else ''
            lg = re.search(r'tvg-logo="([^"]*)"', line)
            logo = lg.group(1) if lg else ''
            name = line.split(',', 1)[-1].strip() if ',' in line else ''
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].startswith('#')):
                j += 1
            url = lines[j].strip() if j < len(lines) else ''
            if url.startswith(('http://', 'https://')):
                out.append((group, name, url, logo, line))
            i = j + 1
        else:
            i += 1
    return out


def probe(url, timeout=7):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read(12000).decode('utf-8', 'replace')
        return '#EXTM3U' in body
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='已有 m3u')
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-probe', action='store_true')
    ap.add_argument('--jobs', type=int, default=40)
    args = ap.parse_args()

    exists = parse_m3u(open(args.base, encoding='utf-8', errors='replace').read())
    exist_names = {e[1] for e in exists}
    exist_urls = {e[2] for e in exists}
    print('已有: %d 线路 / %d 频道名' % (len(exists), len(exist_names)))

    # 只补央视和卫视
    want = re.compile(r'^(CCTV|CGTN|.*卫视)')
    cands = []
    seen = set()
    for url, tag in POOLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                text = r.read().decode('utf-8', 'replace')
        except Exception as e:
            print('  拉取失败 %s: %s' % (url, type(e).__name__))
            continue
        items = parse_m3u(text)
        print('  %s -> %d 条' % (url.split('/')[-1], len(items)))
        for group, name, u, logo, raw in items:
            nm = norm_name(name)
            if not want.match(nm):
                continue
            if u in exist_urls or u in seen:
                continue
            seen.add(u)
            grp = '央视' if nm.startswith(('CCTV', 'CGTN')) else '卫视'
            cands.append((grp, nm, u, logo))

    print('候选补充: %d 条' % len(cands))
    if not cands:
        print('无可补充线路')
        # 原样复制
        open(args.out, 'w', encoding='utf-8', newline='\n').write(
            open(args.base, encoding='utf-8', errors='replace').read())
        return 0

    if not args.no_probe:
        alive = []
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(probe, c[2]): c for c in cands}
            for f in cf.as_completed(futs):
                if f.result():
                    alive.append(futs[f])
        print('  实测存活: %d / %d' % (len(alive), len(cands)))
        cands = alive

    # 统一合并后排序: 央视 -> 卫视 -> 地方
    merged = []   # (group, name, url, logo)
    for grp, nm, u, logo in cands:
        merged.append((grp, nm, u, logo))
    for group, name, u, logo, raw in exists:
        g = group if group in ('央视', '卫视') else (
            '央视' if name.startswith(('CCTV', 'CGTN')) else
            '卫视' if name.endswith('卫视') else '地方')
        merged.append((g, name, u, logo))

    gor = {'央视': 0, '卫视': 1}

    def sk(t):
        g, nm, u, _ = t
        m = re.match(r'CCTV[- ]?(\d+)', nm, re.I)
        num = int(m.group(1)) if m else (
            900 if nm.startswith('CCTV') else 999)
        return (gor.get(g, 2), num, nm)

    merged.sort(key=sk)
    # 同频道多线路按 url 去重
    seen2 = set()
    final = []
    for t in merged:
        k = (t[1], t[2])
        if k in seen2:
            continue
        seen2.add(k)
        final.append(t)

    name_count = {}
    lines = ['#EXTM3U']
    for grp, nm, u, logo in final:
        name_count[nm] = name_count.get(nm, 0) + 1
        tag = '' if name_count[nm] == 1 else ' #%d' % name_count[nm]
        lg = (' tvg-logo="%s"' % logo) if logo else ''
        lines.append('#EXTINF:-1 group-title="%s"%s,%s%s'
                     % (grp, lg, nm, tag))
        lines.append(u)

    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print('合并完成: %d 线路 -> %s' % (len(final), args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
