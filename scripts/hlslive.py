#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播性判定: 只有真的能下到一个分片, 才算这条线路可播

背景
----
过去验活只看「能不能取到 m3u8」。实测发现 gslb 网关会返回**空壳播放列表**:
播放列表合法、#EXT-X-MEDIA-SEQUENCE 一直在滚、但里面指向的 .ts 全部 404。
播放器拿不到分片, 用户看到的往往是 App 自带垫片/广告 —— 这正是
「CCTV-1 多个线路全是假的、是广告线路」的直接原因。

因此这里把判定标准收紧到分片级:
  1. 取播放列表 (master 则下钻到 media playlist, 自引/循环直接判死)
  2. 取最后一个分片, 用 Range 只拉前 4KB
  3. 拿到 TS 同步字节(0x47) / fMP4(styp|moof) / 至少 1KB 有效数据 才算 ok

对直连 TS (URL 本身就是流) 直接按内容类型判定。
"""
import re
import ssl
import urllib.parse
import urllib.request

UA = 'okhttp/3.12.0'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PLAYLIST_MAX = 256 * 1024
SEG_SNIFF = 4096


def _get(url, headers=None, timeout=8, max_bytes=PLAYLIST_MAX, rng=None):
    """返回 (status, body, ctype, final_url); 失败抛异常"""
    h = dict(headers or {})
    h.setdefault('User-Agent', UA)
    if rng:
        h['Range'] = rng
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        body = r.read(max_bytes)
        return (r.status, body, (r.headers.get('Content-Type') or '').lower(),
                r.geturl() or url)


def _is_media_bytes(b):
    """前几个字节能不能看出是媒体数据"""
    if not b:
        return False
    if b[:1] == b'\x47':                      # MPEG-TS 同步字节
        return True
    if b[4:8] in (b'ftyp', b'styp', b'moof', b'sidx'):  # fMP4
        return True
    if b[:3] == b'ID3':                       # 音频分片
        return True
    if b[:4] == b'\x00\x00\x00\x01' or b[:4] == b'\x00\x00\x00\x0c':
        return True
    return False


def _quality_of(txt):
    """从 master 里取最高清晰度 (height, bandwidth)"""
    best = (0, 0)
    for m in re.finditer(r'#EXT-X-STREAM-INF:([^\n]*)', txt):
        attr = m.group(1)
        r = re.search(r'RESOLUTION=(\d+)x(\d+)', attr)
        b = re.search(r'BANDWIDTH=(\d+)', attr)
        q = (int(r.group(2)) if r else 0, int(b.group(1)) if b else 0)
        if q > best:
            best = q
    return best


def _segments(txt):
    """media playlist 里的分片地址 (按出现顺序)"""
    out = []
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        out.append(s)
    return out


def probe_live(url, headers=None, timeout=8, depth=0, quality=(0, 0)):
    """返回 (ok, detail, quality)

    ok=True 仅当真的取到一个可解码分片。
    """
    if depth > 3:
        return False, 'too-deep', quality
    h = dict(headers or {})
    h.setdefault('User-Agent', UA)
    try:
        status, body, ctype, final = _get(url, h, timeout)
    except urllib.error.HTTPError as e:
        return False, 'HTTP%d' % e.code, quality
    except Exception as e:
        return False, type(e).__name__, quality

    # 直连流
    if ('video/' in ctype or 'mp2t' in ctype or 'octet-stream' in ctype) and \
            not body.startswith(b'#EXTM3U'):
        if len(body) < 64:
            return False, 'empty-stream', quality
        return True, 'raw-media', quality or (1080, 0)

    if not body.startswith(b'#EXTM3U'):
        if _is_media_bytes(body):
            return True, 'raw-media', quality or (1080, 0)
        return False, 'not-m3u8:' + ctype[:16], quality

    txt = body.decode('utf-8', 'replace')
    if '#EXT-X-ENDLIST' in txt:
        return False, 'vod-endlist', quality

    q = _quality_of(txt) or quality

    # master -> 选最高码率变体继续
    if '#EXT-X-STREAM-INF' in txt:
        segs = _segments(txt)
        if not segs:
            return False, 'master-no-variant', q
        vurl = urllib.parse.urljoin(final, segs[0])
        if vurl.split('?')[0] == final.split('?')[0]:
            return False, 'self-ref', q          # kankanlive 这类自己指自己
        return probe_live(vurl, h, timeout, depth + 1, q)

    segs = _segments(txt)
    if not segs:
        return False, 'no-segment', q

    seg = urllib.parse.urljoin(final, segs[-1])
    try:
        st, sbody, sct, _ = _get(seg, h, timeout, max_bytes=SEG_SNIFF,
                                 rng='bytes=0-%d' % (SEG_SNIFF - 1))
    except urllib.error.HTTPError as e:
        return False, 'seg-http%d' % e.code, q
    except Exception as e:
        return False, 'seg-' + type(e).__name__, q

    if sbody.startswith(b'#EXTM3U'):
        return probe_live(seg, h, timeout, depth + 1, q)   # 嵌套播放列表
    if _is_media_bytes(sbody):
        return True, 'segment:%d' % len(sbody), q
    if len(sbody) >= 1024:
        return True, 'segment:%d?' % len(sbody), q
    if len(sbody) == 0:
        return False, 'seg-empty', q
    return False, 'seg-tiny:%d' % len(sbody), q
