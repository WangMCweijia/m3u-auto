# -*- coding: utf-8 -*-
"""gslb 网关「轮盘」门禁: 连续多次都指向同一个真源, 才准进订阅。

为什么需要这道门
----------------
`38.75.136.137:98/gslb/{dsdqbv,dsdqpub}/<ename>.m3u8` 这类网关不是 CDN,
而是个**轮盘**: 同一条 URL 每次请求都可能落到不同目标 ——

    302 -> 142.54.162.26:82/live/cctv13md.m3u8?...   真源 (好)
    302 -> 47.97.252.137:81/applive/yss.m3u8         广告池 (坏)
    200 -> {"error":"频道 cctvmd 没有配置任何服务器"}  死链 (坏)

2026-11-11 实测 25 条 URL × 5 轮: 真源约 70% / 死链约 23% / 广告池约 7%;
2026-11-09 实测 cctv6hd 连取 8 次有 6 次落广告池。**比例天天变, 结构不变。**

单次验活必然被骗: 撞上真源就判活, 用户播放时大概率吃广告。所以判据换成
「稳定性」—— 连续 N 轮, 每轮都必须

  1. 真的取到可解码分片 (走 hlslive.probe_live, 分片级判定)
  2. 302 后的最终流不属于广告池 (applive / status / 黑名单 stream)
  3. 最终流的 stream_key 与其余轮次完全一致

任何一轮不满足 -> 整条 URL 判死。

命中广告 / 指向不稳的 URL 会写进 deny 并保留 14 天, 期间不再重测 ——
既省掉每轮构建的重复请求, 也避免"这次撞上真源就放进来"的反复试探。
通过的 URL 24 小时内沿用结论, 不重复抽。

用法
----
    import gslbgate
    passed = gslbgate.gate(items)        # 就地裁剪 items
    adfilter.apply(items, allow=passed)  # 让白名单穿过 /gslb/ 的整体封禁
"""
import datetime
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import hlslive
import sharedstream

# 每条 URL 抽几轮。6 轮 = 至少 6 次独立 302 + 分片下载。
# 实测网关插广告是按时间窗的: 4 轮的版本上线后复核 10 条, 仍有 2 条在第 5 轮
# 和第 1 轮抽中 applive —— 所以要抽得更久、间隔更开 (GAP 1s, 覆盖不同时间窗),
# 而不是挤在 1 秒内问 4 次。
ROUNDS = 6
GAP = 1.0
DENY_TTL_DAYS = 30
PASS_TTL_DAYS = 1
DEFAULT_STORE = 'data/gslb_gate.json'

# 广告池的流标识前缀 (applive/102, applive/yss, status/error_account_pirated ...)
AD_PREFIX = ('applive/', 'status/')


def is_ad_key(key, blocked=None):
    """流标识是不是广告池"""
    if not key:
        return False
    if key.startswith(AD_PREFIX):
        return True
    return bool(blocked) and key in blocked


def shape(url):
    """只看路径的「形状」, 丢掉主机与查询串。

    判「指向是否稳定」必须去主机: 网关会在多个边缘 IP 之间轮换同一个流
    (142.54.162.26 / 204.12.241.18 / 63.141.250.98 都是 /live/cctv6md),
    带主机比会把正常轮换误判成 unstable。

    这里不能复用 sharedstream.stream_key —— 它为了识别广告池刻意保留了
    短路径的主机 (live/cctv6md 这种丢掉 live 后只剩一段, 只能退回主机+路径)。
    """
    if not url:
        return None
    if '://' not in url:
        url = 'http://' + url
    p = urllib.parse.urlparse(url)
    path = re.sub(r'\.(m3u8|m3u|ts|mp4)$', '', p.path or '/', flags=re.I)
    seg = [s for s in path.split('/') if s]
    if not seg:
        return p.netloc.lower()
    return '/'.join(seg[-2:])


def judge(url, rounds=ROUNDS, timeout=8, headers=None, blocked=None):
    """对一条 URL 抽 rounds 轮, 返回 dict(ok, kind, why, key, quality, final)

    kind: ok / ad / dead / unstable
    ok=True 仅当 rounds 轮**全部**取到分片、全部同源、且都不是广告池。
    """
    blocked = sharedstream.load_blocked() if blocked is None else blocked
    keys, quality, final = [], (0, 0), url
    for i in range(max(1, rounds)):
        ctx = {}
        ok, detail, q = hlslive.probe_live(url, headers or {}, timeout, ctx=ctx)
        final = ctx.get('final') or url
        if not ok:
            return dict(ok=False, kind='dead', why='round%d:%s' % (i + 1, detail),
                        key=shape(final), quality=q, final=final)
        if is_ad_key(sharedstream.stream_key(final), blocked):
            return dict(ok=False, kind='ad',
                        why='round%d:ad:%s' % (i + 1, sharedstream.stream_key(final)),
                        key=shape(final), quality=q, final=final)
        keys.append(shape(final))
        if q > quality:
            quality = q
        if i != rounds - 1:
            time.sleep(GAP)
    uniq = sorted(set(k for k in keys if k))
    if len(uniq) > 1:
        return dict(ok=False, kind='unstable',
                    why='unstable:' + '|'.join(u[:32] for u in uniq),
                    key=None, quality=quality, final=final)
    return dict(ok=True, kind='ok', why='', key=uniq[0] if uniq else None,
                quality=quality, final=final)


# ------------------------------------------------------------------ 存档

def _candidates(path):
    yield path
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, '..', 'data', 'gslb_gate.json')


def _read(p):
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault('pass', {})
    d.setdefault('deny', {})
    return d


def load(path=DEFAULT_STORE):
    """读存档。

    只有默认路径才回退到仓库根 —— 显式传入的路径(测试/调试)必须严格按它,
    否则测试会读到仓库里真实的 deny 表, 表现成"一条 URL 都不测"。
    """
    d = _read(path)
    if d is not None:
        return d
    if path != DEFAULT_STORE:
        return {'pass': {}, 'deny': {}}
    for p in _candidates(DEFAULT_STORE):
        d = _read(p)
        if d is not None:
            return d
    return {'pass': {}, 'deny': {}}


def _write(store, p):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def save(store, path=DEFAULT_STORE):
    try:
        _write(store, path)
        return True
    except Exception:
        pass
    if path != DEFAULT_STORE:
        return False
    for p in _candidates(DEFAULT_STORE):
        try:
            _write(store, p)
            return True
        except Exception:
            continue
    return False


def _today():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')


def _fresh(entry, days):
    ts = (entry or {}).get('ts') or ''
    try:
        seen = datetime.date.fromisoformat(ts[:10])
    except ValueError:
        return False
    return (datetime.date.today() - seen).days < days


# ------------------------------------------------------------------ 主流程

def gate(items, path=DEFAULT_STORE, rounds=ROUNDS, jobs=6, timeout=8,
         verbose=True, save_store=True):
    """就地裁剪 items, 只留通过门禁的 gslb 线路; 返回通过的 URL 集合"""
    targets = [it for it in items if '/gslb/' in (it.get('url') or '')]
    if not targets:
        return set()

    store = load(path)
    passed, skipped, retest = set(), 0, []
    for it in targets:
        u = it['url']
        den = (store.get('deny') or {}).get(u)
        if _fresh(den, DENY_TTL_DAYS):
            it['gate_deny'] = (den or {}).get('why')
            skipped += 1
            continue
        ok = (store.get('pass') or {}).get(u)
        if _fresh(ok, PASS_TTL_DAYS):
            it['final'] = (ok or {}).get('final') or u
            it['gate_key'] = (ok or {}).get('key')
            passed.add(u)
            continue
        retest.append(it)

    if verbose:
        print('  gslb 门禁: 候选 %d 条 (沿用 %d / deny 跳过 %d / 待测 %d)'
              % (len(targets), len(passed), skipped, len(retest)), flush=True)

    blocked = sharedstream.load_blocked()
    n_ad = n_unstable = 0
    if retest:
        ex = ThreadPoolExecutor(max_workers=max(1, jobs))
        futs = {ex.submit(judge, it['url'], rounds, timeout, it.get('headers'),
                          blocked): it for it in retest}
        done = 0
        for f in as_completed(futs):
            it = futs[f]
            done += 1
            try:
                r = f.result()
            except Exception as e:
                r = dict(ok=False, kind='dead', why='exc:' + type(e).__name__,
                         key=None, quality=(0, 0), final=None)
            u = it['url']
            if r['ok']:
                it['final'] = r.get('final') or u
                it['gate_key'] = r.get('key')
                q = tuple(r.get('quality') or (0, 0))
                if q > tuple(it.get('quality') or (0, 0)):
                    it['quality'] = q
                passed.add(u)
                store['pass'][u] = dict(key=r.get('key'), final=r.get('final'),
                                        ts=_today(), rounds=rounds)
                store['deny'].pop(u, None)
            elif r['kind'] in ('ad', 'unstable'):
                # 广告 / 指向不稳: 记 deny, 14 天内不再试
                store['deny'][u] = dict(why=(r.get('why') or '')[:120],
                                        ts=_today())
                store['pass'].pop(u, None)
                if r['kind'] == 'ad':
                    n_ad += 1
                else:
                    n_unstable += 1
            # dead: 只代表这次没通, 不写 deny, 下轮构建还会再试
            if verbose and done % 25 == 0:
                print('    门禁 %d/%d (通过 %d)' % (done, len(futs), len(passed)),
                      flush=True)
        ex.shutdown(wait=False)

    items[:] = [it for it in items
                if '/gslb/' not in (it.get('url') or '') or it['url'] in passed]
    if verbose:
        print('  gslb 门禁: 通过 %d / 剔除 %d (广告 %d, 指向不稳 %d)'
              % (len(passed), len(targets) - len(passed), n_ad, n_unstable),
              flush=True)

    # 存档只留当前仍在候选里的 URL, 免得文件无限膨胀
    live = {it['url'] for it in targets}
    for seg in ('pass', 'deny'):
        store[seg] = {k: v for k, v in store[seg].items() if k in live}
    if save_store:
        save(store, path)
    return passed
