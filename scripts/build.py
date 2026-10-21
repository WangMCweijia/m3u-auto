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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channelmeta as cm  # noqa: E402
import gslb as _gslb  # noqa: E402
import adfilter
import hlslive
import m3uio as io_  # noqa: E402
gslb = _gslb

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

STATUS_API = 'http://sj.91kds.cn/lb/get_status_91ktv.php'
SOURCE_BASE = 'http://sj.91kds.cn/lb/'

SKIP_PROTO = ('kdsvod://', 'htmlplay://', 'kdslbk://', 'mitv://', 'sop://',
              'rtsp://', 'rtp://', 'igmp://', 'p3p://')

GROUP_ORDER = cm.GROUP_ORDER      # 央视/卫视/各省/其他

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
        return cm.OTHER_GROUP
    return '网络'


def iter_sources(data):
    """展开为线路列表。

    HD / 标清两套条目 (cctv1hd 与 cctv1) 归并到同一 tvg_id,
    这样播放器里是一个台的多条线路, 而不是两个台。
    """
    seen = set()
    for ch in data:
        ename = (ch.get('ename') or '').strip()
        name = (ch.get('name') or '').strip()
        if not name:
            continue
        prov = (ch.get('prov') or '').strip()
        tvg_id, disp, group = cm.canonical(ename, name, guess_group(ch))
        # 地方台分组用省份, 更贴近用户的找台习惯
        if group in ('地方', '', cm.OTHER_GROUP):
            group = cm.prov_of(name, prov)
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
            key = (tvg_id, url)
            if key in seen:
                continue
            seen.add(key)
            yield dict(ename=ename, tvg_id=tvg_id, name=disp, raw_name=name,
                       group=group, icon=icon, url=url, headers=headers)


# ---------------------------------------------------------------- 验活

def probe(item, timeout=8):
    """返回 (status, detail, quality)

    判定标准已收紧到**分片级** (见 hlslive): 只回 m3u8、分片却 404 的
    "空壳线路" 一律判死 —— 那类线路播放器拿不到画面, 用户看到的是
    App 自己的垫片广告, 也就是「CCTV-1 全是假的」的直接原因。
    """
    h = dict(item.get('headers') or {})
    ok, detail, q = hlslive.probe_live(item['url'], h, timeout)
    if ok:
        return 'ok', detail, q or (0, 0)
    # 超时/域名解析失败留到下一轮重试; 网关偶尔会回 HTML 错误页(限流),
    # 这类也按可重试处理, 否则会把好线路误判成死链
    if (detail in ('TimeoutError', 'URLError', 'timeout', 'socket.timeout')
            or detail.startswith('not-m3u8:text/html')):
        return 'err', detail, (0, 0)
    return 'dead', detail, (0, 0)


def _probe_chunk(chunk, jobs, timeout):
    """测一批, 返回 {chunk 内下标: (status, detail, quality)}

    有硬性时间上限: urllib 的 timeout 不覆盖 DNS 解析, 少数域名会卡住线程,
    超时后直接放弃该批未完成条目, 避免整轮构建挂起。
    """
    res = {}
    ex = cf.ThreadPoolExecutor(max_workers=jobs)
    futs = {ex.submit(probe, it, timeout): i for i, it in enumerate(chunk)}
    budget = max(60, len(chunk) / max(jobs, 1) * (timeout + 2) * 2)
    try:
        for f in cf.as_completed(futs, timeout=budget):
            i = futs[f]
            try:
                res[i] = f.result()
            except Exception as e:
                res[i] = ('err', type(e).__name__, (0, 0))
    except cf.TimeoutError:
        left = sum(1 for f in futs if not f.done())
        print('  批次超时(%.0fs), 放弃 %d 条未完成' % (budget, left), flush=True)
    # 卡在 DNS 的线程无法回收, 不等待
    ex.shutdown(wait=False)
    return res


def run_probe(items, jobs=40, timeout=8, retry=1, batch=400,
              state_path=None, breaker_min=8):
    """多轮批量验活。

    重试必须批量做 —— 若在 as_completed 循环体里同步重试,
    每条超时条目都会阻塞 timeout 秒, 几十条就把整轮拖成几十分钟。

    主机熔断: 同一主机已经验了 breaker_min 条以上且一条都没活,
    后面的批次直接跳过它。3000 个候选里大量是同一个死站的不同频道,
    不熔断就有大半时间耗在必然失败的请求上。
    """
    import verifystate as vs

    # 已带验活结论的直接计入, 不重复测。注意 'cached' 不在其中 ——
    # 缓存线路必须重验, 否则 7 天前的死链会一路进订阅。
    pre = [it for it in items if it.get('status') in ('ok', 'playlist-only')]
    items = [it for it in items
             if it.get('status') not in ('ok', 'playlist-only')]
    total = len(items)
    pending = list(range(total))

    st = vs.load(state_path) if state_path else {}
    try:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')
    except Exception:
        now = ''

    def host_of(it):
        try:
            return urllib.parse.urlparse(it['url']).netloc.lower()
        except Exception:
            return ''

    host_ok, host_fail, broken = {}, {}, set()
    status = {}

    for rnd in range(retry + 1):
        if not pending:
            break
        if rnd:
            print('  重试第 %d 轮: %d 条' % (rnd, len(pending)), flush=True)
        still = []
        for bi in range(0, len(pending), batch):
            idxs = pending[bi:bi + batch]
            chunk, keep = [], []
            for i in idxs:
                h = host_of(items[i])
                if h and h in broken:
                    status[i] = ('dead', 'breaker', (0, 0))
                    continue
                chunk.append(items[i])
                keep.append(i)
            if not chunk:
                continue
            res = _probe_chunk(chunk, jobs, timeout)
            for k, i in enumerate(keep):
                if k in res:
                    status[i] = res[k]
                else:
                    still.append(i)       # 批次超时未测到
            # err 的留到下一轮再来一次
            for k in range(len(keep)):
                if k in res and res[k][0] == 'err':
                    still.append(keep[k])
            # 累计每主机战绩, 决定是否熔断
            for k, i in enumerate(keep):
                if k not in res:
                    continue
                h = host_of(items[i])
                if not h:
                    continue
                if res[k][0] in ('ok', 'playlist-only'):
                    host_ok[h] = host_ok.get(h, 0) + 1
                elif res[k][0] == 'dead':
                    host_fail[h] = host_fail.get(h, 0) + 1
                if (h not in broken
                        and host_ok.get(h, 0) == 0
                        and host_fail.get(h, 0) >= breaker_min):
                    broken.add(h)
            done = sum(1 for v in status.values()
                       if v[0] in ('ok', 'playlist-only'))
            print('  ...已测 %d/%d, 存活 %d, 熔断主机 %d'
                  % (len(status), total, done, len(broken)), flush=True)
        pending = list(dict.fromkeys(still))

    out = list(pre)
    for i, (r, detail, q) in status.items():
        it = items[i]
        if state_path:
            vs.mark(st, it['url'], r in ('ok', 'playlist-only'), now)
            it['steady'] = vs.steady(st, it['url'])
        if r in ('ok', 'playlist-only'):
            it = dict(it)
            it['status'] = r
            it['quality'] = q
            out.append(it)
    if state_path:
        try:
            vs.save(state_path, st)
        except Exception as e:
            print('  验活状态写入失败: %s' % e, flush=True)
    ok = sum(1 for v in status.values() if v[0] == 'ok') + len(pre)
    print('  存活 %d (其中可取分片 %d) / %d, 熔断 %d 个主机'
          % (len(out), ok, total + len(pre), len(broken)), flush=True)
    return out


# ---------------------------------------------------------------- 输出

def write_outputs(items, meta, outdir, epg_map=None, epg_url=''):
    os.makedirs(outdir, exist_ok=True)
    # 统一字段名给 m3uio
    for it in items:
        it['logo'] = it.get('logo') or it.get('icon') or ''
    items = io_.group_and_sort(items)

    io_.write_m3u(os.path.join(outdir, 'live.m3u'), items,
                  epg=epg_map, epg_url=epg_url)
    io_.write_tvlist(os.path.join(outdir, 'live.txt'), items)

    # json: 按频道聚合, urls 保留顺序(清晰度降序)
    chans = {}
    order = []
    for it in items:
        tid = it['tvg_id']
        if tid not in chans:
            chans[tid] = dict(tvgId=tid, name=it['name'], group=it['group'],
                              logo=it.get('logo', ''), ename=it.get('ename', ''),
                              urls=[])
            order.append(tid)
        c = chans[tid]
        if it['url'] not in c['urls']:
            c['urls'].append(it['url'])
        if len(it.get('ename') or '') < len(c.get('ename') or '') or not c.get('ename'):
            c['ename'] = it.get('ename') or c.get('ename') or ''
    payload = dict(meta=meta,
                   channelCount=len(chans),
                   lineCount=len(items),
                   channels=[chans[t] for t in order])
    with open(os.path.join(outdir, 'channels.json'), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # stats
    from collections import Counter
    multi = sum(1 for c in chans.values() if len(c['urls']) > 1)
    stats = dict(
        meta=meta,
        totalLines=len(items),
        totalChannels=len(chans),
        multiLineChannels=multi,
        avgLinesPerChannel=round(len(items) / max(len(chans), 1), 2),
        byGroup=dict(Counter(it['group'] for it in items)),
        byChannelTop=sorted(
            ((c['name'], len(c['urls'])) for c in chans.values()),
            key=lambda x: -x[1])[:30],
        bySource=dict(Counter(
            ('gslb:' + (it.get('via') or '').split(':')[1])
            if (it.get('via') or '').startswith('gslb:') else 'direct'
            for it in items)),
    )
    with open(os.path.join(outdir, 'stats.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print('  写出: live.m3u (%d 频道 / %d 线路, %d 个多线路频道)'
          % (len(chans), len(items), multi), flush=True)
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
    ap.add_argument('--no-gslb', dest='gslb', action='store_false',
                    help='跳过 gslb 网关采集')
    ap.add_argument('--gslb-jobs', type=int, default=4,
                    help='gslb 探测并发 (实测 >20 会被网关限流)')
    ap.add_argument('--gslb-all', dest='gslb_only', action='store_const',
                    const='all', default='priority',
                    help='gslb 扫描全部 ename (默认只扫央视/卫视)')
    ap.add_argument('--gslb-cache', default='data/gslb_cache.json',
                    help='gslb 命中结果缓存 (网关限流时的兜底)')
    ap.add_argument('--verify-state', default='data/verify_state.json',
                    help='线路验活历史 (稳定性排序 + 主机熔断依据)')
    ap.add_argument('--no-epg', dest='epg', action='store_false',
                    help='不处理节目单')
    ap.add_argument('--epg-cache', default='data/epg_map.json')
    ap.add_argument('--epg-refresh', action='store_true',
                    help='强制重新拉取 EPG 频道表')
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

    # 用频道表自带的 prov 反推「地名前缀 -> 省份」, 供公开源池的条目套用
    prov_map = cm.build_prov_map(data)
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/prov_map.json', 'w', encoding='utf-8') as f:
            json.dump(prov_map, f, ensure_ascii=False, indent=0, sort_keys=True)
        print('  省份前缀映射 %d 条' % len(prov_map), flush=True)
    except Exception as e:
        print('  省份映射写出失败: %s' % e, flush=True)

    print('[2/4] 解析直连线路', flush=True)
    items = list(iter_sources(data))
    if args.limit:
        items = items[:args.limit]
    print('  直连线路 %d 条' % len(items), flush=True)

    # gslb 网关: 救回 kdsvod 里那批已失效域名承载的频道 (央视/卫视为主)
    pre_ok = []
    if args.gslb:
        print('[2.5/4] 采集 gslb 网关线路', flush=True)
        try:
            g = gslb.collect(data, jobs=args.gslb_jobs, timeout=args.timeout,
                             only=args.gslb_only, cache_path=args.gslb_cache)
            pre_ok = g
            items = items + g
            print('  合计线路 %d 条 (含 gslb %d)' % (len(items), len(g)), flush=True)
        except Exception as e:
            print('  gslb 采集失败: %s: %s' % (type(e).__name__, e), flush=True)

    # 广告黑名单: 能播但内容是广告的站 (人工看画面确认过的)
    adfilter.apply(items)

    if not items:
        print('  没有可用的直连线路, 退出', flush=True)
        return 1

    if args.no_probe:
        print('[3/4] 跳过验活 (--no-probe)', flush=True)
        for it in items:
            it['status'] = it.get('status') or 'unverified'
            it.setdefault('quality', (0, 0))
        alive = items
    else:
        print('[3/4] 验活 (%d 并发)' % args.jobs, flush=True)
        alive = run_probe(items, jobs=args.jobs, timeout=args.timeout,
                          state_path=args.verify_state)
        print('  存活 %d / %d' % (len(alive), len(items)), flush=True)

    print('[4/4] 生成订阅', flush=True)
    epg_map, epg_url = {}, ''
    if args.epg:
        try:
            import epg as _epg
            epg_url, epg_map = _epg.prepare(alive, args.epg_cache,
                                            force=args.epg_refresh)
        except Exception as e:
            print('  EPG 处理失败: %s: %s' % (type(e).__name__, e), flush=True)
    stats = write_outputs(alive, meta, args.out, epg_map, epg_url)

    print('\n完成. 分组分布:')
    for g, n in sorted(stats['byGroup'].items(),
                       key=lambda x: -x[1])[:12]:
        print('  %-8s %d' % (g, n))
    sys.stdout.flush()
    # 验活中卡在 DNS 的线程无法回收, 直接退出避免进程挂起
    os._exit(0)


if __name__ == '__main__':
    sys.exit(main())
