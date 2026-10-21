#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gslb 网关采集

背景
----
91kds 频道表里的主线路是 `kdsvod://http://v.91kds.com/b7/jsb.m3u8?id=<ename>&pwd=jsdecode`,
而 `v.91kds.com` 已解析到 Linode 美国段且 TCP 不通, 4082 条 kdsvod 线路全废。

但公开源池里出现了同款 id 的第三方网关:
    http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv1hd
    http://38.75.136.137:98/gslb/dsdqbv/cctv1hd.m3u8?auth=test20251009
这两个网关直接接受 91kds 的 ename, 返回标准 HLS 直播切片。
实测 2976 个 ename 中有 310 个可用, 覆盖 CCTV-1~17 全套、5+、4K/8K
以及 47 个卫视, 正好补上央视/卫视缺线路的问题。

用法:
  python gslb.py --list                 # 扫描并打印可用频道
  python gslb.py --check cctv1hd        # 单条检查
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channelmeta as cm
import hlslive  # noqa: E402

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# (名称, url 模板) —— 模板里 %s 换成 ename
GATEWAYS = [
    ('zbdq5', 'http://63.141.230.178:82/gslb/zbdq5.m3u8?id=%s'),
    ('dsdqbv', 'http://38.75.136.137:98/gslb/dsdqbv/%s.m3u8?auth=test20251009'),
    ('dsdqpub', 'http://38.75.136.137:98/gslb/dsdqpub/%s.m3u8?auth=testpub'),
]


def _quality_of_url(url):
    """从 ename 或分片目录名推断清晰度"""
    tag = ''
    m = re.search(r'[?&]id=([a-z0-9]+)', url)
    if m:
        tag = m.group(1)
    else:
        m = re.search(r'/([a-z0-9]+)\.m3u8', url)
        if m:
            tag = m.group(1)
    if tag.endswith('4k'):
        return (2160, 0)
    if tag.endswith('hd'):
        return (1080, 0)
    if tag.endswith('md'):
        return (720, 0)
    return (720, 0)


def check(url, timeout=8, retry=2, gap=0.6):
    """返回 (ok, quality)

    必须是**真能下到分片**才算可用。网关会返回播放列表合法、分片却 404 的
    空壳响应, 只看 m3u8 会把这类线路当成活的, 用户点了就是黑屏/垫片广告。
    """
    import time
    for attempt in range(retry + 1):
        ok, detail, q = hlslive.probe_live(url, {}, timeout)
        if ok:
            return True, (q if q and q[0] else _quality_of_url(url))
        # 4xx/5xx 是确定性失败, 不值得重试
        if 'HTTP' in detail or 'http4' in detail:
            return False, (0, 0)
        if attempt < retry:
            time.sleep(gap)
    return False, (0, 0)


def scan_enames(enames, template, jobs=20, timeout=8):
    """并发测一批 ename, 返回 {ename: quality}"""
    res = {}

    def work(e):
        url = template % e
        ok, q = check(url, timeout)
        return e, ok, q

    ex = cf.ThreadPoolExecutor(max_workers=jobs)
    futs = {ex.submit(work, e): e for e in enames}
    try:
        for f in cf.as_completed(futs):
            try:
                e, ok, q = f.result()
            except Exception:
                continue
            if ok:
                res[e] = q
    finally:
        ex.shutdown(wait=False)
    return res


_PRIORITY = re.compile(r'^(cctv|cetv)', re.I)


def is_priority(ename, ch):
    """央视 / 卫视 / 央视付费频道 —— gslb 网关能救回的主要是这批

    地方台本来就有大量 91kds 直连线路, 不必占用网关配额。
    """
    if _PRIORITY.match(ename or ''):
        return True
    if ename in cm.CCTV_ALIAS:
        return True
    name = ch.get('name') or ''
    prov = ch.get('prov') or ''
    return '卫视' in name or prov in ('央视', '卫视')


CACHE_TTL_DAYS = 7


def load_cache(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path, encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cache(path, cache):
    if not path:
        return
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _prune(cache, days=CACHE_TTL_DAYS):
    import datetime
    cutoff = (datetime.datetime.utcnow()
              - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    return {e: v for e, v in cache.items()
            if (v.get('lastSeen') or '') >= cutoff}


def collect(data, jobs=4, timeout=8, gateways=None, verbose=True,
            only='priority', cache_path=None, also_md=True):
    """从频道表数据生成 gslb 线路

    返回 items 列表 (已带 status='ok' / quality), 结构与 build.iter_sources 一致。
    only='priority' 只扫央视/卫视 (推荐); only='all' 扫全部 ename。

    网关会限流 —— 短时间内重复全量扫描时命中数会从 96 掉到 24。
    因此命中结果写入 cache_path, 本次没扫到的频道沿用缓存里的线路作备选。

    also_md=True 时补扫 <base>md 变体。频道表里只有 hd / 无后缀,
    但网关实际同时提供 md 流 (cctv1md / zjwsmd), 多这一条等于多一条兜底线路,
    清晰度 720p 会被排在 HD 之后, 不影响优先播放。
    """
    import datetime
    gws = gateways or GATEWAYS
    idx = {}
    for ch in data:
        e = (ch.get('ename') or '').strip()
        if e:
            idx[e] = ch
    enames = list(idx)
    if only == 'priority':
        enames = [e for e in enames if is_priority(e, idx[e])]

    if also_md:
        qre = re.compile(r'(hd|4k|8k|sd)$', re.I)
        extra = {}
        for e in enames:
            m = qre.sub('', e) + 'md'
            if m.lower() != e.lower() and m not in idx and m not in extra:
                extra[m] = idx[e]
        for m, ch in extra.items():
            idx[m] = ch
        if extra:
            enames = enames + list(extra)
            if verbose:
                print('  补扫 md 变体 %d 个' % len(extra), flush=True)

    if verbose:
        print('  候选 ename %d 个 (only=%s)' % (len(enames), only), flush=True)

    # 先用主网关扫一遍, 拿到候选集合
    first = gws[0]
    if verbose:
        print('  [%s] 扫描...' % first[0], flush=True)
    hit = scan_enames(enames, first[1], jobs, timeout)
    if verbose:
        print('  [%s] 可用 %d' % (first[0], len(hit)), flush=True)

    # 其余网关只测已命中的, 省时间也避免触发限流
    used = {}          # ename -> [(gw_name, quality)]
    for e, q in hit.items():
        used.setdefault(e, []).append((first[0], q))
    for name, tpl in gws[1:]:
        if not hit:
            break
        if verbose:
            print('  [%s] 扫描 %d 个已命中 ename...' % (name, len(hit)), flush=True)
        r = scan_enames(list(hit), tpl, jobs, timeout)
        if verbose:
            print('  [%s] 可用 %d' % (name, len(r)), flush=True)
        for e, q in r.items():
            used.setdefault(e, []).append((name, q))

    # ---- 缓存合并
    now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    today = now[:10]
    cache = _prune(load_cache(cache_path))
    gwtpl = dict((n, t) for n, t in gws)
    from_cache = {}
    for e, lst in used.items():
        cache[e] = dict(urls=[gwtpl[g] % e for g, _ in lst], lastSeen=now)
    for e, v in cache.items():
        if e not in used and e in idx:
            from_cache[e] = v.get('urls') or []
    save_cache(cache_path, cache)
    if verbose and from_cache:
        print('  缓存补充 %d 个频道 (本次未扫到)' % len(from_cache), flush=True)

    out = []
    for e, lst in used.items():
        ch = idx[e]
        name = (ch.get('name') or '').strip()
        prov = (ch.get('prov') or '').strip()
        tvg_id, disp, group = cm.canonical(e, name)
        if group in ('地方', '', cm.OTHER_GROUP):
            group = cm.prov_of(name, prov)
        icon = ch.get('icon') or ''
        lst.sort(key=lambda x: -x[1][0])
        for gw, q in lst:
            out.append(dict(ename=e, tvg_id=tvg_id, name=disp, raw_name=name,
                            group=group, icon=icon, logo=icon,
                            url=gwtpl[gw] % e, headers={},
                            status='ok', quality=q, via='gslb:' + gw))
    # 缓存里本次没验到的, 作为低优先级备选
    seen_url = {o['url'] for o in out}
    for e, urls in from_cache.items():
        ch = idx[e]
        name = (ch.get('name') or '').strip()
        prov = (ch.get('prov') or '').strip()
        tvg_id, disp, group = cm.canonical(e, name)
        if group in ('地方', '', cm.OTHER_GROUP):
            group = cm.prov_of(name, prov)
        icon = ch.get('icon') or ''
        for u in urls:
            if u in seen_url:
                continue
            seen_url.add(u)
            out.append(dict(ename=e, tvg_id=tvg_id, name=disp, raw_name=name,
                            group=group, icon=icon, logo=icon,
                            url=u, headers={},
                            status='ok', quality=(0, 0), via='gslb:cache'))
    if verbose:
        print('  gslb 线路合计 %d (%d 个频道, 其中缓存 %d)'
              % (len(out), len(used) + len(from_cache), len(from_cache)),
              flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--table', default='../kds/channellist.txt')
    ap.add_argument('--check')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--jobs', type=int, default=20)
    ap.add_argument('--timeout', type=int, default=8)
    ap.add_argument('--out')
    ap.add_argument('--cache', default='../data/gslb_cache.json')
    args = ap.parse_args()

    if args.check:
        for name, tpl in GATEWAYS:
            ok, q = check(tpl % args.check, args.timeout)
            print('  %-8s %s %s' % (name, 'OK' if ok else 'FAIL', q))
        return 0

    data = json.loads(open(args.table, encoding='utf-8').read())
    items = collect(data, args.jobs, args.timeout, cache_path=args.cache)
    items.sort(key=lambda x: (x['group'], x['name']))
    if args.out:
        json.dump(items, open(args.out, 'w'), ensure_ascii=False, indent=1)
        print('写出', args.out)
    else:
        for it in items:
            print('%-10s %-22s %-12s %s'
                  % (it['name'], it['ename'], it['group'], it['url']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
