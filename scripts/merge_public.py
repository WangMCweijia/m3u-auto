#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并公开源池到已有订阅

用途:
  91kds 表里央视/卫视几乎全走已停服的 kdsvod 协议, 直连线路少;
  用公开池 (iptv-org / fanmingming ipv4+ipv6) 为每个台补充线路。

归并规则:
  公开池的频道名先经 channelmeta 归一化成 tvg_id, 再与 base 的 tvg_id 对齐,
  补充线路并入同一频道。这样播放器里仍是"一个台多条线路"。

用法:
  python merge_public.py --base out/live.m3u --out out/live.m3u
  python merge_public.py --base out/live.m3u --out out/live.m3u --no-probe
"""
import argparse
import concurrent.futures as cf
import os
import re
import sys
import ssl
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channelmeta as cm  # noqa: E402
import adfilter  # noqa: E402
import hlslive  # noqa: E402
import m3uio as io_  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

POOLS = [
    # iptv-api 每日自动测活的成品池, 质量最好 (含大量可用央视频道)
    ('https://cdn.jsdelivr.net/gh/Guovin/iptv-api@gd/output/result.m3u', 'guovin'),
    ('https://cdn.jsdelivr.net/gh/zbefine/iptv@main/iptv.m3u', 'zbefine'),
    ('https://iptv-org.github.io/iptv/countries/cn.m3u', 'iptv-org'),
    ('https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/m3u/index.m3u', 'fm-index'),
    ('https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/m3u/ipv4.m3u', 'fm-ipv4'),
    ('https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/m3u/ipv6.m3u', 'fm-ipv6'),
    ('https://cdn.jsdelivr.net/gh/vbskycn/iptv@master/tv/iptv4.m3u', 'vbskycn4'),
]


def probe(url, timeout=7):
    """返回 (ok, quality)

    与 build.probe 一致: 必须真的取到一个分片。只回 m3u8 而分片 404 的
    空壳线路不算活 (否则会把「能播但其实是垫片广告」的线路当成好线路)。
    """
    ok, detail, q = hlslive.probe_live(url, {}, timeout)
    return (ok, q or (0, 0))


def batch_probe(cands, jobs, timeout, batch=400):
    """分批验活, 每批硬性超时, 防止 DNS 卡死拖垮整轮"""
    alive = []
    for bi in range(0, len(cands), batch):
        chunk = cands[bi:bi + batch]
        ex = cf.ThreadPoolExecutor(max_workers=jobs)
        futs = {ex.submit(probe, c['url'], timeout): c for c in chunk}
        budget = max(90, len(chunk) / max(jobs, 1) * (timeout + 3) * 3)
        try:
            for f in cf.as_completed(futs, timeout=budget):
                ok, q = f.result()
                if ok:
                    c = dict(futs[f])
                    c['quality'] = q
                    c['status'] = 'ok'
                    alive.append(c)
        except cf.TimeoutError:
            print('  批次 %d-%d 超时, 放弃 %d 条'
                  % (bi, bi + len(chunk), sum(1 for f in futs if not f.done())))
        ex.shutdown(wait=False)
        print('  ...%d/%d 存活 %d' % (min(bi + batch, len(cands)),
                                      len(cands), len(alive)), flush=True)
    return alive


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='已有 m3u')
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-probe', action='store_true')
    ap.add_argument('--jobs', type=int, default=40)
    ap.add_argument('--timeout', type=int, default=7)
    ap.add_argument('--only-cctv-ws', action='store_true',
                    help='只补央视和卫视 (默认补全池)')
    args = ap.parse_args()

    # 省份前缀映射: build 阶段从频道表生成, 这里载入后公开池条目也能按省份分组
    cm.load_prov_map('data/prov_map.json')

    base = io_.parse_m3u(open(args.base, encoding='utf-8',
                              errors='replace').read())
    print('已有: %d 线路 / %d 频道' % (len(base), len({b['tvg_id'] for b in base})))
    exist_urls = {b['url'] for b in base}
    exist_ids = {b['tvg_id'] for b in base}

    want = re.compile(r'^(CCTV|CGTN|.*卫视)')
    cands = []
    seen = set()
    for url, tag in POOLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
                text = r.read().decode('utf-8', 'replace')
        except Exception as e:
            print('  拉取失败 %-10s %s: %s' % (tag, type(e).__name__, url[:50]))
            continue
        items = io_.parse_m3u(text)
        added = 0
        for it in items:
            if not it['tvg_id']:
                continue
            if args.only_cctv_ws and not want.match(it['name']):
                continue
            # 只要中文台: 央视/卫视/含中文名的地方台
            if not (want.match(it['name']) or re.search(r'[\u4e00-\u9fa5]', it['name'])):
                continue
            if it['url'] in exist_urls or it['url'] in seen:
                continue
            seen.add(it['url'])
            cands.append(it)
            added += 1
        print('  %-10s -> 候选 +%d' % (tag, added))

    print('候选补充: %d 条' % len(cands))
    if not cands:
        print('无可补充线路, 原样输出')
        open(args.out, 'w', encoding='utf-8', newline='\n').write(
            open(args.base, encoding='utf-8', errors='replace').read())
        return 0

    # 公开池里也混着广告站, 先按黑名单剔掉再验活, 省时间
    adfilter.apply(cands)

    if not args.no_probe:
        alive = batch_probe(cands, args.jobs, args.timeout)
        print('  实测存活: %d / %d' % (len(alive), len(cands)))
        cands = alive
    else:
        for c in cands:
            c['quality'] = (0, 0)
            c['status'] = 'unverified'

    merged = base + cands
    # 只保留目标频道 (原有频道 + 新补充的央视卫视中文台)
    # 第二次写盘也要带 EPG 映射, 否则 build 阶段刚换上的 tvg-id 会被打回小写
    epg_url, epg_map = '', {}
    try:
        import epg as _epg
        epg_url, epg_map = _epg.for_items(merged, 'data/epg_map.json')
    except Exception as e:
        print('  EPG 映射跳过: %s: %s' % (type(e).__name__, e))
    result = io_.write_m3u(args.out, merged, epg=epg_map, epg_url=epg_url)

    ids = {r['tvg_id'] for r in result}
    new_ids = ids - exist_ids
    print('合并完成: %d 频道 / %d 线路 (新增频道 %d)'
          % (len(ids), len(result), len(new_ids)))
    from collections import Counter
    cnt = Counter(r['tvg_id'] for r in result)
    multi = sum(1 for v in cnt.values() if v > 1)
    print('  多线路频道: %d / %d' % (multi, len(ids)))
    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    sys.exit(main())
