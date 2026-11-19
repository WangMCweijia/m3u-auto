"""91kds `get_source.php` 直连源采集。

接口需要 App 的签名参数（见 ApiUtils.addStartParams / Utils.getNwTime）：
  tm  = 当前 Unix 秒
  key = md5(salt1 + tm + salt2)
其余参数用固定/占位值即可，服务端只校验 key。

返回的 liveSource 里混有：
  * kdsvod://utuxx.91kds.cn/...  —— 需要 jsdecode，跳过
  * http://host:port/udp/239.x    —— 运营商组播代理，外网/CI 不可用，跳过
  * 真正的 http(s) HLS            —— 采纳（kankan、央视官方、hunancatv 等）
"""

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

SALT1 = '4954af3c86d8bc0b766afee71503d860'
SALT2 = 'f8dd806a73202456eb6e782c1c4aecfc'
API = 'http://sj.91kds.cn/api/get_source.php?'
UA = 'okhttp/3.12.0'


def _sign(tm=None):
    tm = int(tm or time.time())
    key = hashlib.md5(('%s%d%s' % (SALT1, tm, SALT2)).encode()).hexdigest()
    return tm, key


def _query(ename, tm, key):
    q = dict(ename=ename, app='91ktv', version='2.2.6', channel='umeng',
             deviceId='a1b2c3d4e5f60718', ipv6Support='no', isHaveFile='no',
             userToken='', imei='123456', mac='02:00:00:00:00:00',
             netType='wifi', app_desc='', tm=str(tm), key=key)
    return API + urllib.parse.urlencode(q)


def fetch(ename, timeout=15):
    """返回 API 的 JSON；被限流/投毒时抛 RuntimeError。"""
    tm, key = _sign()
    req = urllib.request.Request(_query(ename, tm, key),
                                 headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
    urls = data.get('liveSource') or []
    first = str(urls[0]) if urls else ''
    if '盗用' in first or '接口' in first:
        raise RuntimeError('接口限流: %s' % first[:60])
    return data


def _split(raw):
    """'url@@referer=https://x/@@' -> (url, headers)"""
    parts = raw.split('@@')
    url = parts[0].strip()
    headers = {}
    for p in parts[1:]:
        if '=' not in p:
            continue
        k, v = p.split('=', 1)
        k, v = k.strip(), v.strip()
        if k:
            headers[k] = v
    return url, headers


def _usable(url):
    if not url.startswith(('http://', 'https://')):
        return False
    if '/udp/' in url.lower():
        return False
    return True


def candidates(ename, timeout=15):
    """该 ename 下可直接播放的 HLS 线路 [(name, url, headers)]"""
    data = fetch(ename, timeout=timeout)
    out = []
    for name, raw in zip(data.get('liveSourceName') or [],
                         data.get('liveSource') or []):
        url, headers = _split(str(raw))
        if _usable(url):
            out.append((str(name), url, headers))
    return out


def _load_cache(path):
    """缓存格式: {ename: {url: {name, headers, ts}}}

    兼容旧格式 {ename: {ts, data}}。
    """
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for ename, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        if 'data' in rec:
            ts = int(rec.get('ts') or 0)
            data = rec.get('data') or {}
            merged = {}
            for name, r in zip(data.get('liveSourceName') or [],
                               data.get('liveSource') or []):
                url, headers = _split(str(r))
                if _usable(url):
                    merged[url] = dict(name=str(name), headers=headers, ts=ts)
            out[ename] = merged
        else:
            out[ename] = rec
    return out


def _save_cache(path, cache):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def collect(channels, cache_path=None, max_age=4 * 3600, timeout=8,
            delay=0.12, attempts=2, budget=150, verbose=True):
    """从频道表里筛出的频道批量抓直连 HLS。

    channels: [{'ename','name','prov','group','icon',...}]
    返回可直接并入 build.items 的 dict 列表。

    接口每次返回的线路会有出入（hunancatv 时有时无），所以缓存按 URL
    累积：每次成功抓取都把新线路并进去，max_age 内出现过的都算有效。
    """
    import channelmeta as cm

    cache = _load_cache(cache_path) if cache_path else {}
    now = time.time()
    start = now
    seen_names = set()
    seen_urls = set()
    items = []
    stats = dict(queried=0, failed=0, skipped=0, urls=0)
    for ch in channels:
        ename = (ch.get('ename') or '').strip()
        if not ename or ename in seen_names:
            continue
        seen_names.add(ename)
        rec = cache.get(ename) or {}
        # hunancatv 这类直连线路在接口里时有时无 (实测 1/6 ~ 4/6),
        # 一轮只有 kdsvod/组播时重抽，直到拿到至少一条直连 HLS
        if time.time() - start <= budget:
            for attempt in range(attempts):
                added = 0
                try:
                    data = fetch(ename, timeout=timeout)
                    stats['queried'] += 1
                    for name, raw in zip(data.get('liveSourceName') or [],
                                         data.get('liveSource') or []):
                        url, headers = _split(str(raw))
                        if _usable(url):
                            rec[url] = dict(name=str(name), headers=headers,
                                            ts=int(now))
                            added += 1
                    time.sleep(delay)
                except Exception as e:
                    stats['failed'] += 1
                    if verbose:
                        print('  kdsapi %s 失败: %s: %s'
                              % (ename, type(e).__name__, e), flush=True)
                if added:
                    break
                time.sleep(delay * 2)
        else:
            stats['skipped'] += 1
        cache[ename] = rec
        fresh = [(u, v) for u, v in rec.items()
                 if now - int(v.get('ts') or 0) <= max_age]
        if not fresh:
            fresh = sorted(rec.items(),
                           key=lambda kv: -int(kv[1].get('ts') or 0))[:12]
        tvg_id, disp, group = cm.canonical(
            ename, ch.get('name') or '', ch.get('group') or '')
        for url, v in fresh:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            items.append(dict(
                ename=ename, tvg_id=tvg_id, name=disp,
                raw_name=ch.get('name') or '', group=group,
                icon=ch.get('icon') or '', url=url,
                headers=v.get('headers') or {},
                via='kdsapi', quality_hint=v.get('name') or ''))
            stats['urls'] += 1
    _save_cache(cache_path, cache)
    if verbose:
        print('  kdsapi: 查询 %d, 失败 %d, 跳过 %d, 直连 HLS %d'
              % (stats['queried'], stats['failed'], stats['skipped'],
                 stats['urls']), flush=True)
    return items


if __name__ == '__main__':
    import sys
    for e in (sys.argv[1:] or ['cctv1hd']):
        print('==', e)
        for n, u, h in candidates(e):
            print(' ', n, u, h)
