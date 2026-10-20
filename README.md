# m3u-auto

自动聚合并实测存活的公开直播源，每 6 小时重建一次。

## 订阅地址

| 格式 | 地址 |
|---|---|
| M3U（推荐） | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/out/live.m3u` |
| TVLIST | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/out/live.txt` |
| 索引页 | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/out/index.html` |

备用（无 CDN 缓存，可能较慢）：
`https://raw.githubusercontent.com/<owner>/<repo>/main/out/live.m3u`

## 数据来源

1. **公开频道表** `sj.91kds.cn` —— 拉取频道清单与线路，无鉴权
2. **iptv-org / fanmingming** —— 补全央视与卫视

所有线路在构建时逐条 HTTP 实测，只保留真正能取到播放列表的。

## 结构

```
scripts/build.py          拉频道表 -> 解析 -> 验活 -> 生成订阅
scripts/merge_public.py   合并公开源池补全央视/卫视
scripts/genindex.py       生成索引页
.github/workflows/update.yml  定时任务
out/                      构建产物
```

## 本地运行

```bash
python scripts/build.py --out out            # 拉表 + 验活
python scripts/build.py --local table.zip    # 用本地频道表
python scripts/build.py --no-probe           # 跳过验活，快速生成
```

## 已知限制

- 91kds 频道表中央视/卫视主线路走 `kdsvod://` 私有协议，依赖的 `v.91kds.com` 域名已失效，
  因此这两类频道主要依靠公开源池补全。
- 地方台存活率明显高于央视/卫视（地方广电 CDN 多为公网直连）。
- 线路随时可能失效，订阅重建后会自动剔除。

## 声明

本项目只做公开流地址的索引与可用性测试，不托管、不转码、不分发任何音视频内容。
所有内容版权归原权利人所有。
