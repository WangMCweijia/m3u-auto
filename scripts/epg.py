#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EPG (节目单) 对齐

播放器只按 tvg-id 去 xmltv 里找节目单, 所以 tvg-id 必须等于 EPG 的 channel id。
我们的内部 id 是语义化的 (cctv1 / zjws / sdws), 而 EPG 用的是
`CCTV1` / `浙江卫视` 这类写法 —— 因此写出前做一次映射, 内部 id 保持小写
(归并、分组、排序都依赖它), 只在 EXTINF 里换成 EPG 的 id。

映射规则是确定性的, 不用人工维护表:
  1. 显示名正好等于某个 EPG id  -> 用它        (浙江卫视 -> 浙江卫视)
  2. tvg_id 形如 cctv<数字>      -> CCTV<数字>  (cctv1 -> CCTV1)
  3. CCTV5+ 这类带后缀的         -> 单独处理
  4. 其余保持原样 (EPG 没这个台, 加了 x-tvg-url 也不影响播放)

EPG 文件很大 (6MB+), 因此映射结果落盘缓存 7 天, 不是每次构建都拉。
"""
import json
import os
import re
import time
import urllib.request

SOURCES = [
    'https://cdn.jsdelivr.net/gh/fanmingming/live@main/e.xml',
    'https://raw.githubusercontent.com/fanmingming/live/main/e.xml',
]
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
MAX_BYTES = 12 * 1024 * 1024
REFRESH_DAYS = 7
DEFAULT_SOURCE = SOURCES[0]


def fetch_ids(url=None, timeout=40, max_bytes=MAX_BYTES):
    """拉一段 xmltv, 抠出全部 <channel id="...">; 返回 (ids, 实际用的 url)"""
    for u in ([url] if url else SOURCES):
        try:
            req = urllib.request.Request(u, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = r.read(max_bytes)
            txt = buf.decode('utf-8', 'replace')
            ids = re.findall(r'<channel\s+id="([^"]+)"', txt)
            if ids:
                return set(ids), u
        except Exception:
            continue
    return set(), ''


def map_one(tvg_id, name, ids):
    """内部 id + 显示名 -> EPG channel id; 对不上返回 ''"""
    nm = (name or '').strip()
    if nm and nm in ids:
        return nm
    tid = (tvg_id or '').strip()
    low = tid.lower()
    m = re.fullmatch(r'cctv(\d{1,2})', low)
    if m:
        cand = 'CCTV%d' % int(m.group(1))
        if cand in ids:
            return cand
    if low in ('cctv5p', 'cctv5plus'):
        for cand in ('CCTV5+', 'CCTV5PLUS'):
            if cand in ids:
                return cand
    # 英文名台 (CGTN 系列) 在 EPG 里是中文, 按显示名再试一次
    if nm and nm.upper() in {i.upper() for i in ids}:
        for i in ids:
            if i.upper() == nm.upper():
                return i
    return ''


def _fresh(path, days=REFRESH_DAYS):
    try:
        return (time.time() - os.path.getmtime(path)) < days * 86400
    except Exception:
        return False


def cached(path='data/epg_map.json'):
    """读现成的缓存, 返回 (url, map); 没有就返回 ('', {})"""
    d = load(path)
    if d.get('map'):
        return d.get('url') or DEFAULT_SOURCE, d['map']
    return '', {}


def load(path):
    """返回 {'url':..., 'map': {tvg_id: epg_id}, 'ts':...}"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get('map'), dict):
            return d
    except Exception:
        pass
    return {'url': '', 'map': {}, 'ts': ''}


def save(path, url, mapping):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    payload = {'url': url, 'ts': time.strftime('%Y-%m-%d %H:%M'),
               'map': mapping}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, path)


def prepare(items, cache_path='data/epg_map.json', verbose=True,
            force=False):
    """确保有可用的映射; 返回 (url, {tvg_id: epg_id})

    缓存新鲜就直接用; 过期才重新拉 EPG。拉不到时退回旧缓存, 全都没有就放弃
    EPG (不影响订阅本身)。
    """
    cached = load(cache_path)
    if not force and cached['map'] and _fresh(cache_path):
        if verbose:
            print('  EPG 映射: 缓存 %d 个频道 (源 %s)'
                  % (len(cached['map']), cached.get('url') or '?'), flush=True)
        return cached.get('url') or DEFAULT_SOURCE, cached['map']

    ids, url = fetch_ids()
    if not ids:
        if cached['map']:
            if verbose:
                print('  EPG 拉取失败, 沿用缓存 %d 个频道' % len(cached['map']),
                      flush=True)
            return cached.get('url') or DEFAULT_SOURCE, cached['map']
        if verbose:
            print('  EPG 拉取失败, 本次不带节目单', flush=True)
        return '', {}

    mapping = {}
    for it in items:
        tid = it.get('tvg_id') or ''
        if not tid or tid in mapping:
            continue
        m = map_one(tid, it.get('name') or '', ids)
        if m:
            mapping[tid] = m
    try:
        save(cache_path, url, mapping)
    except Exception:
        pass
    if verbose:
        print('  EPG 映射: %d 个频道可显示节目单 (EPG 共 %d 个 id)'
              % (len(mapping), len(ids)), flush=True)
    return url, mapping


if __name__ == '__main__':
    ids, u = fetch_ids()
    print('EPG id %d 个, 源 %s' % (len(ids), u))
    print(sorted(ids)[:20])
