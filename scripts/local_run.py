#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地一键跑完整流水线 (GitHub Actions 的兜底)

Actions 出问题 (额度、限流、上游抽风) 时, 在本机跑一遍就能拿到同样的产物:

    python scripts/local_run.py              # 只生成到 out/
    python scripts/local_run.py --push       # 生成后提交并推回仓库

产物与 CI 完全一致: out/live.m3u / out/live.txt / out/index.html
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(name, cmd):
    print('\n=== %s ===' % name, flush=True)
    t0 = time.time()
    rc = subprocess.call(cmd, cwd=ROOT)
    print('--- %s %s (%.0fs)' % (name, 'ok' if rc == 0 else 'rc=%d' % rc,
                                 time.time() - t0), flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='out')
    ap.add_argument('--jobs', type=int, default=40)
    ap.add_argument('--timeout', type=int, default=9)
    ap.add_argument('--local', help='用本地频道表 zip, 不联网拉')
    ap.add_argument('--no-gslb', action='store_true')
    ap.add_argument('--no-probe', action='store_true')
    ap.add_argument('--push', action='store_true', help='提交并推回远端')
    args = ap.parse_args()

    py = sys.executable
    b = [py, 'scripts/build.py', '--out', args.out,
         '--jobs', str(args.jobs), '--timeout', str(args.timeout)]
    if args.local:
        b += ['--local', args.local]
    if args.no_gslb:
        b += ['--no-gslb']
    if args.no_probe:
        b += ['--no-probe']

    if run('构建订阅', b) != 0 and not args.no_probe:
        print('验活模式失败, 退回 --no-probe')
        run('构建订阅(跳过验活)', b + ['--no-probe'])

    run('合并公开源池',
        [py, 'scripts/merge_public.py', '--base', os.path.join(args.out, 'live.m3u'),
         '--out', os.path.join(args.out, 'live.m3u')])
    run('生成索引页', [py, 'scripts/genindex.py', '--out', args.out])

    m3u = os.path.join(ROOT, args.out, 'live.m3u')
    if os.path.exists(m3u):
        print('\n产物: %s (%d bytes)' % (m3u, os.path.getsize(m3u)))

    if args.push:
        run('提交并推送', ['git', 'add', 'out/', 'data/'])
        rc = subprocess.call(['git', 'commit', '-m',
                              'chore: 本地更新 %s' % time.strftime('%Y-%m-%d %H:%M')],
                             cwd=ROOT)
        if rc == 0:
            run('推送', ['git', 'push', 'origin', 'HEAD:main'])
        else:
            print('没有变更, 无需提交')


if __name__ == '__main__':
    main()
