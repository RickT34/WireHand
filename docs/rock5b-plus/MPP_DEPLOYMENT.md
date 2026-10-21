# ROCK 5B+：MPP H.264 驱动部署手册

> 目录重构说明：当前安装与启动命令见 [平台部署说明](README.md)。下文的实机版本、性能数字和备份路径是 2026-10-19 的记录；本轮重构未重新部署到板子。

依据 2026-10-19 在 `ssh r5p` 上执行并验证的操作整理。适用基线为 **ROCK 5B+ / RK3588 / Armbian Debian 13 trixie / arm64**。本手册部署内核和编解码运行环境；KVM 应用、密码、USB gadget、EDID 的首次安装见项目 README。本次实际结果见 [MPP_SETUP.md](MPP_SETUP.md)。

命令已按重新部署的顺序整理，增加了动态设备查找、超时和状态恢复；它不是逐字终端转录。文档中的 shell 代码已做语法检查，本轮整理没有再次安装软件或重启机器。

## 1. 部署版本与操作顺序

| 组件 | 本次使用版本或路径 |
| --- | --- |
| 原内核 | `6.18.28-current-rockchip64` |
| vendor 内核与设备树包 | `linux-image-vendor-rk35xx=26.8.3`、`linux-dtb-vendor-rk35xx=26.8.3` |
| 重启后内核 | `6.1.115-vendor-rk35xx` |
| 用户态组件 | `jellyfin-ffmpeg7=7.1.4-3-trixie`，含 MPP/RGA 运行库 |
| 硬件 FFmpeg | `/usr/lib/jellyfin-ffmpeg/ffmpeg`，别名 `ffmpeg-mpp` |
| 设备接口 | `/dev/mpp_service`、`/dev/rga`、`/dev/dma_heap/*` |

操作顺序：检查系统 → 备份 → 准备软件源 → 下载并安装内核和用户态 → 核对启动文件 → 重启 → 设备/合成编码/硬件解码/真实 HDMI 验证 → 恢复并检查应用。

这些版本是已经验证的组合。以后若仓库不再提供相同版本，先用 `apt-cache policy` 确定可用的内核和匹配设备树版本，再重新完成验证流程。

## 2. 登录、检查与网络

以下安装代码块均在 **r5p 的 root shell** 中按顺序执行；控制机命令会另行标注。

```sh
ssh r5p
```

```sh
set -eu
uname -a
cat /etc/os-release
tr '\0' '\n' </proc/device-tree/compatible
dpkg --print-architecture
df -h / /boot
lsblk -f
ls -l /boot/Image /boot/uInitrd /boot/dtb
cat /boot/armbianEnv.txt
systemctl --failed --no-pager
apt-cache policy linux-image-vendor-rk35xx linux-dtb-vendor-rk35xx
```

本次设备确认为 `radxa,rock-5b-plus`、`rockchip,rk3588`，根分区在 NVMe，`/boot` 与根分区共用空间。原主线内核只有 V4L2 编解码接口，没有 `/dev/mpp_service`。仅安装 MPP 用户态库不能补上此内核接口。

以下命令需要 `curl`、CA 证书、`initramfs-tools`、`v4l-utils`；设备树检查另用 `device-tree-compiler`。本次系统已具备这些工具，新系统缺少时先从 Debian 源安装。

本次 r5p 通过控制机的局域网代理下载依赖：

```sh
export http_proxy=http://192.168.50.1:7890
export https_proxy=http://192.168.50.1:7890
```

能直连时省略代理。`127.0.0.1:7890` 在远端表示 r5p 自己，并不指向控制机。代理只通过当前 shell 环境传给下载命令，没有写入系统持久配置。

## 3. 备份与回退准备

在安装内核包之前执行。每次使用新的备份目录，保留原内核包和 `/lib/modules`。

```sh
backup_dir="/var/backups/kvm-mpp-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup_dir"
cp -a /boot "$backup_dir/"
cp -a /etc/armbian-release "$backup_dir/"
for item in /etc/kvm-mcp.env /etc/netplan /opt/kvm-mcp/src/kvm_mcp/platforms/rock5b_plus/video.py; do
    if [ -e "$item" ]; then cp -a "$item" "$backup_dir/"; fi
done
uname -r >"$backup_dir/original-kernel.txt"
dpkg-query -W >"$backup_dir/packages-before.txt"
apt-mark showhold >"$backup_dir/holds-before.txt"
printf '%s\n' "$backup_dir"
```

记录最后输出的路径。原启动链接保存在备份的 `boot/` 内；恢复时必须同时恢复 Image、uInitrd 和 dtb，避免内核与设备树混用。本次已有备份目录为 `/var/backups/kvm-mpp-20261019/`。

## 4. 安装带 MPP 的 FFmpeg

使用 Jellyfin 官方签名仓库，只安装 FFmpeg 包。以下配置适用于 Debian 13 arm64；不适用于直接复制到其他发行版。

```sh
install -d -m 755 /etc/apt/keyrings
curl -fsSL --connect-timeout 10 --max-time 60 \
  https://repo.jellyfin.org/jellyfin_team.gpg.key \
  -o /etc/apt/keyrings/jellyfin.asc
chmod 644 /etc/apt/keyrings/jellyfin.asc
cat >/etc/apt/sources.list.d/jellyfin.sources <<'EOF'
Types: deb
URIs: https://repo.jellyfin.org/debian
Suites: trixie
Components: main
Architectures: arm64
Signed-By: /etc/apt/keyrings/jellyfin.asc
EOF
chmod 644 /etc/apt/sources.list.d/jellyfin.sources
apt-get -o Dir::Etc::sourcelist=sources.list.d/jellyfin.sources \
  -o Dir::Etc::sourceparts=- -o Acquire::https::Timeout=30 update
apt-cache policy jellyfin-ffmpeg7
apt-get -s install jellyfin-ffmpeg7=7.1.4-3-trixie
DEBIAN_FRONTEND=noninteractive apt-get -y \
  -o Acquire::Retries=1 -o Acquire::https::Timeout=30 \
  install jellyfin-ffmpeg7=7.1.4-3-trixie
```

这里定向刷新 Jellyfin 源，是为了避免其他源临时故障阻塞本次安装。新系统若缺依赖，应先正常刷新 Debian 软件源；不关闭签名校验。

建立专用入口。若同名路径已存在，先检查其目标，避免覆盖已有自定义程序：

```sh
test -e /usr/local/bin/ffmpeg-mpp || \
  ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg-mpp
test -e /usr/local/bin/ffprobe-mpp || \
  ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe-mpp
ffmpeg-mpp -hide_banner -encoders 2>/dev/null | grep rkmpp
ffmpeg-mpp -hide_banner -filters 2>/dev/null | grep rkrga
```

预期看到 `h264_rkmpp`、`hevc_rkmpp`、`mjpeg_rkmpp` 和 `scale_rkrga`。此时只能证明程序包含相应接口；重启并实际编码后才能证明硬件工作。系统 `/usr/bin/ffmpeg` 没有被替换。

## 5. 安装匹配的 vendor 内核和设备树

本次使用现有 Armbian 官方源。首先确认它提供目标版本，然后下载到候选包目录。

```sh
install -d /opt/kvm-mcp/driver-candidates
cd /opt/kvm-mcp/driver-candidates
apt-cache policy linux-image-vendor-rk35xx linux-dtb-vendor-rk35xx
apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=1 download \
  linux-image-vendor-rk35xx=26.8.3 linux-dtb-vendor-rk35xx=26.8.3
sha256sum linux-image-vendor-rk35xx_26.8.3_arm64.deb \
  linux-dtb-vendor-rk35xx_26.8.3_arm64.deb
apt-get -s install ./linux-image-vendor-rk35xx_26.8.3_arm64.deb \
  ./linux-dtb-vendor-rk35xx_26.8.3_arm64.deb
DEBIAN_FRONTEND=noninteractive apt-get -y install \
  ./linux-image-vendor-rk35xx_26.8.3_arm64.deb \
  ./linux-dtb-vendor-rk35xx_26.8.3_arm64.deb
```

本次 SHA256 与 APT 元数据一致：

```text
fed812d3b906b91cfa13270a976d860dc9061f520e5e6222248547881d07a29f  linux-image-vendor-rk35xx_26.8.3_arm64.deb
7013c5c4707133d7fa2d7b6c636d55039952137bfa06d85a4f5a875d06e924a8  linux-dtb-vendor-rk35xx_26.8.3_arm64.deb
```

内核包 postinst 会生成 initramfs/uInitrd，并改写 `/boot/Image`、`/boot/uInitrd`、`/boot/dtb` 链接。本次没有修改 U-Boot/SPI 固件，也没有编辑设备树内容。

重启前核对：

```sh
ls -lh /boot/Image /boot/uInitrd /boot/dtb
test -s /boot/vmlinuz-6.1.115-vendor-rk35xx
test -s /boot/uInitrd-6.1.115-vendor-rk35xx
test -s /boot/dtb-6.1.115-vendor-rk35xx/rockchip/rk3588-rock-5b-plus.dtb
grep -E 'CONFIG_(ROCKCHIP_MPP|VIDEO_ROCKCHIP_HDMIRX|USB_DWC3)' \
  /boot/config-6.1.115-vendor-rk35xx
```

本次还用 `dtc` 检查板级设备树：MPP、两个 RKVENC 核心、HDMI RX 均为 `okay`，USB `fc000000` 为 `otg`；并用 `lsinitramfs` 核对了网络/存储相关内容。移植到其他板卡时应检查对应板级设备树，不能只改包名。

## 6. 设备权限与旧分支保护

```sh
cat >/etc/udev/rules.d/70-rockchip-mpp.rules <<'EOF'
# Rockchip MPP/RGA hardware codec access for members of video.
KERNEL=="mpp_service", GROUP="video", MODE="0660"
KERNEL=="rga", GROUP="video", MODE="0660"
SUBSYSTEM=="dma_heap", GROUP="video", MODE="0660"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=dma_heap
udevadm trigger --sysname-match=mpp_service
udevadm trigger --sysname-match=rga
```

非 root 应用账号需加入 `video` 组，并重启进程使组成员关系生效。本次还通过 `runuser -u nobody -g video` 执行短时 H.264 编码，确认权限有效。

本次原内核分支的包名如下。仅在这些包已安装时执行 hold，以免之后更新旧分支再次改写启动链接；不 hold 新 vendor 包。

```sh
apt-mark hold linux-image-current-rockchip64 linux-dtb-current-rockchip64
apt-mark showhold
sync
systemctl reboot
```

本次重启后 SSH 曾暂时报 `No route to host`，随后原地址恢复；没有改网络配置或重新分配地址。重连后再继续：

```sh
ssh r5p
```

```sh
uname -r
ls -l /dev/mpp_service /dev/rga /dev/dma_heap/*
systemctl --failed --no-pager
```

预期内核为 `6.1.115-vendor-rk35xx`，设备权限为 `root:video 0660`。

## 7. 合成编码与硬件解码验收

在 r5p root shell 中执行，不占用 HDMI。

```sh
set -eu
install -d -m 700 /opt/kvm-mcp/diagnostics/mpp
cd /opt/kvm-mcp/diagnostics/mpp
timeout 30 ffmpeg-mpp -nostdin -hide_banner -y \
  -init_hw_device rkmpp=rk -filter_hw_device rk \
  -f lavfi -i testsrc=size=1920x1080:rate=60,format=bgr24 \
  -vf hwupload,scale_rkrga=format=nv12 \
  -frames:v 600 -c:v h264_rkmpp -b:v 8M h264-1080p60.mp4 \
  >encode.log 2>&1
ffprobe-mpp -v error -count_frames \
  -show_entries stream=codec_name,width,height,r_frame_rate,nb_read_frames \
  -of json h264-1080p60.mp4
timeout 30 ffmpeg-mpp -nostdin -v error -y \
  -hwaccel rkmpp -hwaccel_output_format drm_prime -i h264-1080p60.mp4 \
  -vf hwdownload,format=nv12 -f framemd5 decoded.framemd5
grep -cv '^#' decoded.framemd5
```

通过条件：编码、解码均退出 0；ffprobe 报 `h264`、1920×1080、`60/1`、600 帧；framemd5 有 600 条帧记录。此处 framemd5 用于记录解码输出，不表示有损压缩结果与原始帧逐像素相同。

## 8. 真实 HDMI 验收

本次信号为 1920×1080、60 Hz、BGR3。真实采集前先停止占用同一节点的服务。下面这段在**控制机**执行，会保留 KVM 服务原本的运行/停止状态，并动态寻找 HDMI 节点。

```sh
ssh r5p 'bash -s' <<'REMOTE'
set -eu
was_active=0
if systemctl is-active --quiet kvm-web; then was_active=1; fi
restore_service() {
    if [ "$was_active" -eq 1 ]; then systemctl start kvm-web; fi
}
trap restore_service EXIT
if [ "$was_active" -eq 1 ]; then systemctl stop kvm-web; fi
video_device=
for name in /sys/class/video4linux/video*/name; do
    if grep -qi hdmirx "$name"; then
        video_device="/dev/$(basename "$(dirname "$name")")"
        break
    fi
done
test -n "$video_device"
v4l2-ctl -d "$video_device" --query-dv-timings --get-fmt-video
install -d -m 700 /opt/kvm-mcp/diagnostics/mpp
cd /opt/kvm-mcp/diagnostics/mpp
timeout 20 ffmpeg-mpp -nostdin -hide_banner -y \
  -init_hw_device rkmpp=rk -filter_hw_device rk \
  -f v4l2 -input_format bgr24 -video_size 1920x1080 -i "$video_device" \
  -vf hwupload,scale_rkrga=format=nv12 \
  -frames:v 300 -c:v h264_rkmpp -b:v 8M -g 60 \
  -fps_mode passthrough hdmi-h264.mp4 >hdmi-encode.log 2>&1
ffprobe-mpp -v error -count_frames \
  -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_read_frames,duration \
  -of json hdmi-h264.mp4 >hdmi-probe.json
cat hdmi-probe.json
timeout 15 ffmpeg-mpp -nostdin -v error \
  -hwaccel rkmpp -hwaccel_output_format drm_prime -i hdmi-h264.mp4 \
  -vf hwdownload,format=nv12 -f null -
REMOTE
```

先确认查询结果符合上述尺寸和格式；其他输入模式应调整采集参数。本次验收文件为 300 帧、5 秒、1080p60，硬件解码通过。视频包含真实屏幕内容，保存在权限 0700 的远端目录；控制机仅复制了 [探测结果](../../artifacts/mpp/hdmi-probe.json) 和 [编码日志](../../artifacts/mpp/hdmi-encode.log)。

## 9. KVM 应用适配与最终检查

切换内核后，原主线内核的 `v4l2convert` + `v4l2jpegenc` 管线不能直接沿用。本次对 `video.py` 做了这些适配：

- 检测 `/dev/mpp_service`，在 vendor 内核上选择配套 FFmpeg。
- HDMI 用 V4L2 mmap 采集 BGR，再 `hwupload` → `scale_rkrga` → MPP 编码。
- vendor HDMI RX 自动跟踪输入时序，跳过其不支持的 `--set-dv-bt-timings=query`。
- 安装阶段用 `mjpeg_rkmpp` + `mpjpeg` 保持现有 JPEG 网页和 MCP 截图可用；状态明确标记 `dma_input=false`。
- 按时间桶选择帧以限制帧率，输出使用 `-fps_mode passthrough`，避免低帧率源被重复补帧。

重新部署时应同步项目当前版本的应用代码和依赖。后续 H.264/WebRTC 应用代码已有独立演进，不能用本次备份的旧 `video.py` 覆盖较新部署。

```sh
systemctl is-active kvm-web kvm-hdmi kvm-gadget
systemctl --failed --no-pager
cat /sys/class/udc/*/state
journalctl -u kvm-web --no-pager -n 30
```

驱动安装阶段还运行了 `python -m unittest test_kvm test_web`（26 项通过）、`smoke_mcp.py r5p`，以及 JPEG 模式下的 `smoke_web.py --frames 180 --live`。后续若应用采用 WebRTC，应使用对应的 `smoke_rtc.py` 验收；JPEG 帧测试不等同于 WebRTC 验证。真实浏览器交互仍需单独验收。

## 10. 本次遇到的问题

| 现象 | 原因与实际处理 |
| --- | --- |
| 主线内核没有 MPP 节点 | 安装 vendor 内核和匹配设备树，重启后节点出现 |
| APT 下载长时间无输出 | 对下载命令使用可达的局域网代理，限制超时与重试 |
| 猜测 Jellyfin 下载目录返回 404 | 使用官方签名 APT 仓库，不依赖手写下载目录 |
| 重启后暂时无 SSH | 等待启动完成，检查原地址；最终恢复原 IP |
| HDMI 暂时报无信号、USB `not attached` | 后续输入恢复，查询得到 1080p60、USB `configured`；未改设备树，不能仅凭短暂状态认定驱动失败 |
| `VIDIOC_S_DV_TIMINGS: Invalid argument` | vendor HDMI RX 自动跟踪时序，去掉应用对该 ioctl 的设置 |
| `-fpsmax` 与 `-fps_mode vfr` 冲突 | 用 select 帧选择过滤器与 passthrough 输出 |
| 严格按相邻帧间隔限速导致约半帧率 | 时间戳微小抖动使接近阈值的帧被丢弃，改按 `floor(t*fps)` 时间桶选择 |
| 在 SSH heredoc 里 FFmpeg 莫名暂停，下一条命令被截断 | FFmpeg 读取 stdin 消耗了 shell 后续命令；所有此类调用加 `-nostdin` |
| 临时测试服务仍绑定 8080 | `EnvironmentFile` 覆盖了 `systemd-run --setenv`；改在执行命令中用 `/usr/bin/env KVM_PORT=8081 ... python ...` 覆盖 |
| 停止采集时出现 V4L2 buffer/QBUF 警告 | 本次退出码 0、300 帧完整且可硬件解码，重启采集成功；检查实际退出码和产物，不把停止阶段警告等同于编码失败 |

## 11. 回退与维护

本次 r5p 已有专用回退脚本：

```sh
ssh r5p '/var/backups/kvm-mpp-20261019/restore-mainline.sh'
ssh r5p 'systemctl reboot'
```

未来新部署应使用第 3 步生成的备份路径。下面在目标机 root shell 执行，先将路径替换为实际备份目录：

```sh
set -eu
backup_dir=/var/backups/kvm-mpp-YYYYMMDD-HHMMSS
test -f "$backup_dir/boot/armbianEnv.txt"
for item in Image uInitrd dtb; do
    test -L "$backup_dir/boot/$item"
    saved_target=$(readlink "$backup_dir/boot/$item")
    case "$saved_target" in
        /*) test -e "$saved_target" ;;
        *) test -e "/boot/$saved_target" ;;
    esac
done
cp -a --remove-destination "$backup_dir/boot/armbianEnv.txt" /boot/armbianEnv.txt
for item in Image uInitrd dtb; do
    cp -a --remove-destination "$backup_dir/boot/$item" "/boot/$item"
done
sync
systemctl reboot
```

无法 SSH 时，从救援系统挂载原系统分区，修复其 `boot/` 下相同的启动链接。原内核文件和模块仍须存在；本次没有删除它们。

重新使用主线分支后，可对本次设置 hold 的两个旧包执行 `apt-mark unhold` 恢复升级。vendor 内核后续升级可能再次改写启动链接，因此每次升级后都检查内核、设备树和 initramfs 是否匹配，并运行编码/解码验收。

## 12. 本次保存的文件

| 位置 | 内容 |
| --- | --- |
| 本项目 `MPP_DEPLOYMENT.md` | 复用部署步骤与问题记录 |
| 本项目 `MPP_SETUP.md` | 本次安装结果与阶段边界 |
| 本项目 `artifacts/mpp/` | 实机编码日志和视频元数据，不含实际屏幕视频 |
| r5p `/opt/kvm-mcp/driver-candidates/` | 已下载的内核包、设备树包及反编译设备树 |
| r5p `/opt/kvm-mcp/diagnostics/mpp/` | 合成和真实 HDMI 验收产物 |
| r5p `/var/backups/kvm-mpp-20261019/` | 原启动文件、配置、原 video.py 和回退脚本 |
| r5p `/etc/apt/sources.list.d/jellyfin.sources` | 签名软件源配置 |
| r5p `/etc/udev/rules.d/70-rockchip-mpp.rules` | 设备访问权限 |

本次未把访问密码、SSH 密钥或代理凭据写入部署文档。
