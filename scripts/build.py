#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
91kds / 悠兔电视 频道表 -> 标准直播源订阅

用法:
  python build.py                 # 拉官方表 + 验活 + 生成订阅
  python build.py --no-probe      # 只拉表生成, 不验活 (CI 快速模式)
  python build.py --local FILE    # 用本地 channel zip/txt, 不联网拉表
  python build.py --limit 200     # 只处理前 N 条线路 (调试)

输出 (out/ 目录):
  live.m3u        标准 M3U 订阅
  live.txt        91kds/tvlist 原生格式
  channels.json   结构化数据
  stats.json      统计信息

设计要点:
  * 频道表接口完全公开, 无需签名/无需伪装 App:
      GET http://sj.91kds.cn/lb/get_status_91ktv.php
        -> {"sourceVersion":20251030,"sourceName":"channel_ktvjsnd-20251030.zip",...}
      GET http://sj.91kds.cn/lb/<sourceName>
        -> zip 内含 channellist_for_91ktv.txt (JSON)
  * urls 用 '#@' 分隔多条线路; 线路可带 'url@@Referer=https://x/@@' 后缀,
    必须剥离为真实 HTTP 头
  * kdsvod:// / htmlplay:// / sop:// 等私有协议无法直接播放, 跳过
"""
import argparse
import concurrent.futures as cf
import io
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

STATUS_API = 'http://sj.91kds.cn/lb/get_status_91ktv.php'
SOURCE_BASE = 'http://sj.91kds.cn/lb/'

SKIP_PROTO = ('kdsvod://', 'htmlplay://', 'kdslbk://', 'mitv://', 'sop://',
              'rtsp://', 'rtp://', 'igmp://', 'p3p://')

GROUP_ORDER = {'央视': 0, '卫视': 1, '地方': 2, '影视': 3, '体育': 4,
               '少儿': 5, '纪录': 6, '港澳台': 7, '网络': 8, '虎牙': 9}

# ename 里有这些关键字的归到卫视组
WEISHI_HINT = re.compile(r'(weishi|ws|satellite)', re.I)


# ---------------------------------------------------------------- 网络

def http_get(url, headers=None, timeout=20, raw=False):
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        data = r.read()
        return data if raw else data.decode('utf-8', 'replace')


def fetch_status():
    txt = http_get(STATUS_API, timeout=15)
    txt = txt.lstrip('\ufeff')
    return json.loads(txt)


def fetch_channellist(status=None):
    """返回 (channel_dict_list, meta)"""
    st = status or fetch_status()
    name = st.get('sourceName')
    if not name:
        raise RuntimeError('status 接口未返回 sourceName: %r' % st)
    url = SOURCE_BASE + name
    print('  源包: %s' % url, flush=True)
    blob = http_get(url, timeout=60, raw=True)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    # 包里可能只有一个 txt
    target = None
    for n in zf.namelist():
        if n.lower().endswith('.txt'):
            target = n
            break
    if not target:
        raise RuntimeError('源包里没有 txt: %s' % zf.namelist())
    raw = zf.read(target)
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError('无法解码频道表')
    data = json.loads(text.lstrip('\ufeff'))
    meta = {
        'sourceVersion': st.get('sourceVersion'),
        'sourceName': name,
        'serTime': st.get('serTime'),
        'channelCount': len(data),
    }
    return data, meta


# ---------------------------------------------------------------- 解析

def split_params(raw):
    """'url@@K=V@@K2=V2@@' -> (url, headers)"""
    parts = raw.split('@@')
    url = parts[0].strip()
    headers = {}
    for p in parts[1:]:
        p = p.strip()
        if not p or '=' not in p:
            continue
        k, v = p.split('=', 1)
        k, v = k.strip(), v.strip()
        if k:
            headers[k] = v
    return url, headers


def guess_group(ch):
    prov = (ch.get('prov') or '').strip()
    name = ch.get('name') or ''
    ename = ch.get('ename') or ''
    if prov in GROUP_ORDER:
        return prov
    if '卫视' in name or WEISHI_HINT.search(ename):
        return '卫视'
    if re.search(r'CCTV|央视|中央', name, re.I):
        return '央视'
    if prov:
        return '地方'
    return '网络'


def iter_sources(data):
    seen = set()
    for ch in data:
        ename = ch.get('ename') or ''
        name = (ch.get('name') or '').strip()
        if not name:
            continue
        group = guess_group(ch)
        icon = ch.get('icon') or ''
        for raw in (ch.get('urls') or '').split('#@'):
            raw = raw.strip()
            if not raw or raw.startswith(SKIP_PROTO):
                continue
            if not raw.startswith(('http://', 'https://')):
                continue
            url, headers = split_params(raw)
            if not url.startswith(('http://', 'https://')):
                continue
            key = (ename, url)
            if key in seen:
                continue
            seen.add(key)
            yield dict(ename=ename, name=name, group=group, icon=icon,
                       url=url, headers=headers)


# ---------------------------------------------------------------- 验活

def probe(item, timeout=8):
    """返回 (status, detail)。ok 表示能取到含分片的播放列表"""
    h = {'User-Agent': UA}
    h.update(item['headers'])
    url = item['url']
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read(30000)
            ctype = (r.headers.get('Content-Type') or '').lower()
    except urllib.error.HTTPError as e:
        return 'dead', 'HTTP%d' % e.code
    except Exception as e:
        return 'err', type(e).__name__

    # 直接是媒体分片
    if 'video/' in ctype or 'mp2t' in ctype:
        return 'ok', 'raw-media'

    txt = body.decode('utf-8', 'replace')
    if '#EXTM3U' not in txt:
        return 'dead', 'not-m3u8:' + ctype[:20]

    # master -> 拉变体
    if '#EXT-X-STREAM-INF' in txt:
        m = re.search(r'#EXT-X-STREAM-INF[^\n]*\n([^\n#]+)', txt)
        if m:
            vurl = urllib.parse.urljoin(url, m.group(1).strip())
            try:
                req = urllib.request.Request(vurl, headers=h)
                with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r2:
                    txt = r2.read(30000).decode('utf-8', 'replace')
                url = vurl
            except Exception:
                pass

    if ('#EXTINF' in txt or '.ts' in txt or '.m4s' in txt
            or '#EXT-X-TARGETDURATION' in txt or '#EXT-X-MEDIA-SEQUENCE' in txt):
        return 'ok', 'hls'

    # 合法空列表 (直播尚未开播/时段性)
    return 'playlist-only', 'no-segments'


def run_probe(items, jobs=40, timeout=8):
    out = []
    total = len(items)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(probe, it, timeout): it for it in items}
        for f in cf.as_completed(futs):
            it = futs[f]
            done += 1
            try:
                st, detail = f.result()
            except Exception as e:
                st, detail = 'err', type(e).__name__
            if st == 'ok':
                it = dict(it)
                it['status'] = st
                out.append(it)
            if done % 200 == 0 or done == total:
                print('  ...%d/%d ok=%d' % (done, total, len(out)), flush=True)
    return out


# ---------------------------------------------------------------- 输出

def sort_key(ch):
    g = GROUP_ORDER.get(ch['group'], 9)
    name = ch['name']
    m = re.match(r'CCTV[- ]?(\d+)', name, re.I)
    num = int(m.group(1)) if m else 999
    return (g, num, name, ch['ename'])


def dedup_by_channel(items):
    """同一频道多条线路都保留, 但按 url 去重; 频道按名称排序"""
    seen = set()
    res = []
    for it in sorted(items, key=sort_key):
        k = (it['ename'], it['url'])
        if k in seen:
            continue
        seen.add(k)
        res.append(it)
    return res


def write_outputs(items, meta, outdir):
    os.makedirs(outdir, exist_ok=True)
    items = dedup_by_channel(items)

    # M3U: 同名频道多线路用同名 + 线路后缀
    lines = ['#EXTM3U']
    counter = {}
    for it in items:
        base = it['name']
        counter[base] = counter.get(base, 0) + 1
        tag = '' if counter[base] == 1 else ' #%d' % counter[base]
        logo = (' tvg-logo="%s"' % it['icon']) if it.get('icon') else ''
        lines.append('#EXTINF:-1 group-title="%s"%s,%s%s'
                     % (it['group'], logo, base, tag))
        lines.append(it['url'])
    m3u_path = os.path.join(outdir, 'live.m3u')
    with open(m3u_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')

    # tvlist (91kds 原生: 分类|频道名,url)
    tlines = []
    for it in items:
        tlines.append('%s|%s,%s' % (it['group'], it['name'], it['url']))
    with open(os.path.join(outdir, 'live.txt'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(tlines) + '\n')

    # json
    chans = {}
    for it in items:
        c = chans.setdefault(it['ename'], dict(
            ename=it['ename'], name=it['name'],
            group=it['group'], icon=it.get('icon', ''), urls=[]))
        c['urls'].append(it['url'])
    payload = dict(meta=meta, count=len(items),
                   channels=list(chans.values()))
    with open(os.path.join(outdir, 'channels.json'), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # stats
    from collections import Counter
    stats = dict(
        meta=meta,
        totalLines=len(items),
        totalChannels=len(chans),
        byGroup=dict(Counter(it['group'] for it in items)),
        byChannelTop=sorted(
            ((v['name'], len(v['urls'])) for v in chans.values()),
            key=lambda x: -x[1])[:30],
    )
    with open(os.path.join(outdir, 'stats.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print('  写出: live.m3u (%d 线路) / live.txt / channels.json / stats.json'
          % len(items), flush=True)
    return stats


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='out')
    ap.add_argument('--no-probe', action='store_true', help='跳过验活')
    ap.add_argument('--local', help='本地频道表 zip 或 txt 路径')
    ap.add_argument('--jobs', type=int, default=40)
    ap.add_argument('--timeout', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0, help='限制线路数, 调试用')
    args = ap.parse_args()

    print('[1/4] 获取频道表', flush=True)
    if args.local:
        p = args.local
        if p.lower().endswith('.zip'):
            zf = zipfile.ZipFile(p)
            target = next(n for n in zf.namelist() if n.lower().endswith('.txt'))
            raw = zf.read(target)
            m = re.search(r'(\d{8})', os.path.basename(p))
            meta = dict(sourceVersion=int(m.group(1)) if m else None,
                        sourceName=os.path.basename(p),
                        serTime=None, channelCount=0)
        else:
            raw = open(p, 'rb').read()
            meta = dict(sourceVersion=None, sourceName=os.path.basename(p),
                        serTime=None, channelCount=0)
        for enc in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                data = json.loads(raw.decode(enc).lstrip('\ufeff'))
                break
            except UnicodeDecodeError:
                continue
        meta['channelCount'] = len(data)
    else:
        data, meta = fetch_channellist()
    print('  频道 %d 个, 源版本 %s' % (len(data), meta.get('sourceVersion')),
          flush=True)

    print('[2/4] 解析直连线路', flush=True)
    items = list(iter_sources(data))
    if args.limit:
        items = items[:args.limit]
    print('  直连线路 %d 条' % len(items), flush=True)
    if not items:
        print('  没有可用的直连线路, 退出', flush=True)
        return 1

    if args.no_probe:
        print('[3/4] 跳过验活 (--no-probe)', flush=True)
        for it in items:
            it['status'] = 'unverified'
        alive = items
    else:
        print('[3/4] 验活 (%d 并发)' % args.jobs, flush=True)
        alive = run_probe(items, jobs=args.jobs, timeout=args.timeout)
        print('  存活 %d / %d' % (len(alive), len(items)), flush=True)

    print('[4/4] 生成订阅', flush=True)
    stats = write_outputs(alive, meta, args.out)

    print('\n完成. 分组分布:')
    for g, n in sorted(stats['byGroup'].items(),
                       key=lambda x: -x[1])[:12]:
        print('  %-8s %d' % (g, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
