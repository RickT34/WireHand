# ROCK 5B+ 部署指南

[返回文档导航](../README.md)

ROCK 5B+ 是 Radxa 推出的 **单板计算机（SBC）**，搭载 Rockchip RK3588 八核处理器（4× Cortex-A76 + 4× Cortex-A55），可运行 Debian、Armbian 等 Linux 系统。它集成 HDMI 输入、USB OTG 和网络接口，适合同时承担画面采集、键鼠模拟及自动化程序运行。在 WireHand 中，板子就是连接被控设备与远程操作端的 KVM 控制器。

[Radxa 官方产品页](https://www.radxa.com/products/rock5/5bp/) · [官方介绍与接口说明](https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction)

![Radxa ROCK 5B+ 官方板卡与接口图](https://docs.radxa.com/img/rock5b/rock5b-plus-overview.webp)

*图片来源：Radxa Computer (Shenzhen) Co., Ltd.，[Radxa 官方文档](https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction)，按站点标注的 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 署名引用；使用官方原图链接，未修改图片。*

**被控设备无需安装任何控制软件。** ROCK 5B+ 通过 HDMI IN 采集画面，通过 USB gadget 模拟标准 HID 键盘鼠标。具有兼容视频输出、能够接收 USB 键鼠输入的电子设备即可按此方式接入，控制对象不限于电脑。

网页服务、图像识别和录制脚本运行在板子上。被控设备只需输出画面并接收键鼠输入，控制过程不依赖其操作系统提供远程控制 API。

适配代码位于 `src/wirehand/platforms/rock5b_plus/`，部署模板位于 `deploy/rock5b-plus/`。本指南的主机名 `rock-kvm` 是示例，替换为自己的 SSH 地址或别名。

## 接线与系统条件

1. 被控设备 HDMI OUT → ROCK 5B+ HDMI IN。
2. 被控设备 USB 主机口 → 板子的全功能 USB-C 数据口。
3. 板子的独立 USB-C PD 供电口连接电源。
4. 首次调试使用单屏或复制模式、1080p60 输入和 US 键盘布局。

板端需要 Linux、Python 3.11+、HDMI RX V4L2 驱动、USB device controller / configfs，以及对应的视频编码环境。常用用户态工具可在 Debian 系统中安装：

```bash
apt-get update
apt-get install python3-venv v4l-utils gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

MPP H.264 路径还需要匹配的 Rockchip vendor 内核、MPP/RGA 设备接口和支持 `h264_rkmpp` 的 FFmpeg。普通发行版 FFmpeg 不一定包含这些组件。适配器同时保留主线内核的 GStreamer RGA/JPEG 路径。

检查设备能力：

```bash
v4l2-ctl --list-devices
ls /sys/class/udc
ls /dev/mpp_service /dev/rga
/usr/lib/jellyfin-ffmpeg/ffmpeg -encoders | grep h264_rkmpp
```

最后两项用于 MPP 路径。已验证过的内核和 FFmpeg 组合、安装及回退过程记录在 [历史 MPP 部署手册](../history/rock5b-plus/MPP_DEPLOYMENT.md)。选择系统镜像和驱动时按实际版本核对，历史手册中的固定版本号不是当前软件仓库的可用性承诺。

## 安装应用

将仓库放到板子的 `/opt/kvm-mcp`。下面的系统配置和服务模板按 root 用户部署；使用其他用户时，需要另外配置设备和目录权限。

```bash
cd /opt/kvm-mcp
python3 -m venv .venv
.venv/bin/python -m pip install .
```

已安装 uv 时可改用 `uv sync --frozen`。安装整个项目后，`wirehand-web`、`wirehand-mcp`、静态资源和 EDID 都会就位。

从模板创建配置，并生成随机密码：

```bash
python3 - <<'PY'
import os
import secrets
from pathlib import Path

template = Path('deploy/rock5b-plus/kvm.env.example').read_text()
config = template.replace('KVM_TOKEN=\n', 'KVM_TOKEN=' + secrets.token_urlsafe(32) + '\n', 1)
fd = os.open('/etc/kvm-mcp.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(config)
PY
```

配置已存在时命令会退出，不覆盖原密码。默认网页监听板子的 `127.0.0.1:8080`；密码保存在 `/etc/kvm-mcp.env`。需要局域网直接访问时，调整 `KVM_BIND`，并把实际浏览器访问的 IP/域名加入 `KVM_HOSTS`。

安装并启动服务：

```bash
install -m 644 deploy/rock5b-plus/kvm-gadget.service /etc/systemd/system/
install -m 644 deploy/rock5b-plus/kvm-hdmi.service /etc/systemd/system/
install -m 644 deploy/rock5b-plus/wirehand-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kvm-gadget kvm-hdmi wirehand-web
systemctl status wirehand-web kvm-gadget kvm-hdmi --no-pager
```

`kvm-gadget` 初始化 USB HID，`kvm-hdmi` 配置 EDID，`wirehand-web` 启动控制台。对应手工初始化命令是 `rock5b-plus-gadget start|stop` 和 `rock5b-plus-hdmi [--restore]`。

## 打开控制台

在自己的电脑上建立 SSH 隧道：

```bash
ssh -N -L 18080:127.0.0.1:8080 root@rock-kvm
```

打开 <http://127.0.0.1:18080/>，用 `/etc/kvm-mcp.env` 中的密码登录。接管、录制与脚本管理见 [网页控制台](../WEB_CONSOLE.md)。设置 HTTPS 和调整视频模式见 [远程访问](../REMOTE_VIEWING.md)。

MCP 使用 [SSH 配置示例](../../deploy/rock5b-plus/mcp-config.json)，详细说明见 [MCP 接入](../MCP.md)。网页、SDK 和 SSH MCP 需要使用相同用户及 `/run/kvm-mcp`；systemd 的 `EnvironmentFile` 不会自动传到 SSH 会话。

## 平台配置

通用配置见 [配置参考](../CONFIGURATION.md)。以下变量由 ROCK 适配器读取：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `KVM_VIDEO_DEVICE` | 动态发现 | 指定 HDMI RX V4L2 节点 |
| `KVM_VIDEO_MODE` | `hardware` | `hardware` 或 `software` |
| `KVM_VIDEO_SOURCE` | 真实输入 | `test` 使用合成源，仍需要对应编码工具 |
| `KVM_TRANSPORT` | `h264` | vendor 输出 H.264；`jpeg` 只输出 JPEG |
| `KVM_MAX_FPS` | `60` | 1–60，不超过输入帧率 |
| `KVM_MAX_WIDTH` / `KVM_MAX_HEIGHT` | `1280` / `720` | 等比缩放上限；宽 320–3840，高 180–2160 |
| `KVM_H264_BITRATE` | `2500000` | 500000–20000000 bit/s |
| `KVM_FFMPEG` | `/usr/lib/jellyfin-ffmpeg/ffmpeg` | 含 MPP/RGA 支持的 FFmpeg |
| `KVM_EDID_FILE` | 包内 `edid/1080p-rgb.bin` | RGB EDID 文件 |

网页采集时，MCP 和 SDK 优先读取两秒内的共享截图；网页没有采集时可以独立截图。录制目标所用分辨率受实际采集输出影响。

## 更新、检查与停用

更新源码后，在板子上重新安装项目并重启网页：

```bash
cd /opt/kvm-mcp
.venv/bin/python -m pip install .
systemctl restart wirehand-web
journalctl -u wirehand-web -n 50 --no-pager
```

在控制机的仓库根目录中，可运行这些探针；参数中的主机名和端口对应前述 SSH 隧道：

```bash
uv run python scripts/smoke_mcp.py root@rock-kvm
uv run python scripts/smoke_web.py --origin http://127.0.0.1:18080 --frames 20 --live
uv run python scripts/smoke_h264.py --origin http://127.0.0.1:18080 --seconds 10
uv run python scripts/smoke_control.py --origin http://127.0.0.1:18080 --check-timeout
```

网页探针从控制机仓库根目录的 `web-access.txt` 读取 `Password: ...` 行。该文件应以权限 `0600` 保存，不提交到 Git。查看和视频探针不点击目标电脑；控制权探针会申请并释放接管，并发送键鼠释放报告。完整的真实输入验收应在确认目标电脑适合测试后进行。

只停用网页：

```bash
systemctl disable --now wirehand-web
```

停用 USB 模拟设备：`systemctl disable --now kvm-gadget`。恢复原 EDID 时，先停止网页和 `kvm-hdmi`，再执行 `/opt/kvm-mcp/.venv/bin/rock5b-plus-hdmi --restore`；原 EDID 保存在 `/var/lib/kvm-mcp/original-edid.bin`。
