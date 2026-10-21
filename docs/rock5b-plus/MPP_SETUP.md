# r5p MPP H.264 安装与验证记录

> 目录重构说明：当前安装与启动命令见 [平台部署说明](README.md)。下文的实机版本、性能数字和备份路径是 2026-10-19 的记录；本轮重构未重新部署到板子。

2026-10-19，主机 `r5p`（192.168.50.152），ROCK 5B+ / RK3588，Debian 13。

这是本次**驱动安装阶段**的操作与验收记录。后续重新部署请按 [MPP_DEPLOYMENT.md](MPP_DEPLOYMENT.md) 操作，其中包含完整安装命令、重启检查、踩坑记录和回退步骤。本文的网页 JPEG 状态及测试数字记录安装当时的结果；后续 H.264/WebRTC 应用改动不在本次记录范围内。

## 已安装

- Armbian `linux-image-vendor-rk35xx=26.8.3`、`linux-dtb-vendor-rk35xx=26.8.3`。
- 已重启进入 `6.1.115-vendor-rk35xx`。MPP、RGA、HDMI RX 驱动随内核提供。
- Jellyfin 官方签名软件源的 `jellyfin-ffmpeg7=7.1.4-3-trixie`，包含 MPP/RGA 用户态运行库与硬件编码器；未安装 Jellyfin 服务。
- `/usr/local/bin/ffmpeg-mpp`、`ffprobe-mpp` 指向 `/usr/lib/jellyfin-ffmpeg/` 下的程序。系统 `/usr/bin/ffmpeg` 保留原版本，调用 MPP 时使用专用命令。
- `/etc/udev/rules.d/70-rockchip-mpp.rules` 将 MPP、RGA、DMA heap 设备权限设为 `root:video 0660`；已用 `nobody:video` 完成硬件编码验证。
- 原主线内核与设备树包保留并设为 hold，避免升级旧分支时改写默认启动链接；vendor 包未 hold。

MPP 要求 vendor 内核和相应设备接口，依据 [ffmpeg-rockchip 项目说明](https://github.com/nyanmisaka/ffmpeg-rockchip#important)；用户态采用 [Jellyfin 的 Rockchip 硬件加速组件](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/rockchip/)。

## 验证结果

| 路径 | 结果 |
| --- | --- |
| 设备接口 | `/dev/mpp_service`、`/dev/rga`、`/dev/dma_heap/*` 存在 |
| 合成 BGR → RGA → H.264 MPP | 1920×1080、60 fps、600 帧，约 141 fps 处理吞吐 |
| 对上述 H.264 执行 MPP 硬件解码 | 600 帧完成，逐帧校验记录已保存 |
| **真实 HDMI → RGA → H.264 MPP** | **1920×1080、60 fps、300 帧、5 秒视频；硬件解码通过** |
| 网页真实 HDMI → MPP JPEG → WebSocket → Python 解码 | 180 帧 / 3.252 秒，约 55.3 fps，平均 JPEG 109,430 字节 |
| 网页合成源完整传输 | 120 帧约 30 fps |
| MCP | 初始化、工具枚举、状态查询通过；USB 为 `configured` |
| 回归检查 | 26 项 Python 测试通过 |

合成吞吐不代表 HDMI 或浏览器端到端延迟。网页测试为 HTTP/WebSocket 协议及图像解码验证，未进行真实浏览器操作验收。

远端证据保存在 `/opt/kvm-mcp/diagnostics/mpp/`（目录权限 0700），包括 `hdmi-h264.mp4`、`hdmi-probe.json`、`hdmi-encode.log`、合成测试视频与 `decoded.framemd5`。真实 HDMI 测试退出码为 0，停止采集时 FFmpeg 有 V4L2 缓冲释放警告；文件帧数和硬件解码完整，重新启动网页采集成功。

## 使用

无须占用 HDMI 的合成编码测试：

```sh
ssh r5p 'ffmpeg-mpp -nostdin -y -f lavfi -i testsrc2=size=1920x1080:rate=60 -frames:v 300 -c:v h264_rkmpp -b:v 8M /tmp/h264-test.mp4'
```

文件转码：

```sh
ffmpeg-mpp -nostdin -i input.mp4 -c:v h264_rkmpp -b:v 8M -c:a copy output.mp4
```

真实 HDMI 测试需要独占采集设备。当前节点为 `/dev/video0`，重启后应通过 `/sys/class/video4linux/video*/name` 中的 `hdmirx` 名称再次确认：

```sh
ssh r5p 'bash -s' <<'REMOTE'
set -eu
systemctl stop kvm-web
trap 'systemctl start kvm-web' EXIT
timeout 20 ffmpeg-mpp -nostdin -y \
  -init_hw_device rkmpp=rk -filter_hw_device rk \
  -f v4l2 -input_format bgr24 -video_size 1920x1080 -i /dev/video0 \
  -vf hwupload,scale_rkrga=format=nv12 \
  -frames:v 300 -c:v h264_rkmpp -b:v 8M -g 60 \
  -fps_mode passthrough /tmp/hdmi-h264.mp4
REMOTE
```

## KVM 兼容调整

`video.py` 在硬件模式检测到 `/dev/mpp_service` 时使用 FFmpeg 的 RGA + `mjpeg_rkmpp` 管线，并跳过 vendor HDMI RX 不支持的 `VIDIOC_S_DV_TIMINGS` 设置。主线内核仍使用原 GStreamer 管线。

驱动安装结束时网页继续传输 JPEG；当时驱动与命令行的 H.264 能力已可用，网页 H.264/WebRTC 传输尚未实现。该阶段的 vendor 采集使用 V4L2 mmap 后上传硬件缓冲，不是原主线管线的端到端 DMA-BUF 导入；状态明确报告 `backend=rkmpp`、`dma_input=false`。

## 回退

远端完整启动文件、原配置和原 `video.py` 已备份到 `/var/backups/kvm-mpp-20261019/`。回到原主线内核：

```sh
ssh r5p '/var/backups/kvm-mpp-20261019/restore-mainline.sh'
ssh r5p 'systemctl reboot'
```

脚本恢复原 `armbianEnv.txt`，并将 Image、uInitrd、dtb 三个启动链接切回 `6.18.28-current-rockchip64`。若无法 SSH，可从救援系统挂载系统分区后修复相同链接。无需卸载 vendor 包即可回退。

确定以后重新使用主线分支并恢复其自动升级时，再执行：

```sh
ssh r5p 'apt-mark unhold linux-image-current-rockchip64 linux-dtb-current-rockchip64'
```
