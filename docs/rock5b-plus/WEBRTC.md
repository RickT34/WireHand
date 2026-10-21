# H.264 WebRTC 控制台

> 目录重构说明：当前安装与启动命令见 [平台部署说明](README.md)。下文的实机版本、性能数字和备份路径是 2026-10-19 的记录；本轮重构未重新部署到板子。

2026-10-19 已部署到 `r5p`，访问地址仍为 `http://192.168.50.152:8080/`，密码不变。

## 实现

- 真实 HDMI：vendor `rk_hdmirx`，动态查找设备，本次 `/dev/video0`，BGR3 / 1920×1080 / 60Hz。
- 使用已有 `/usr/lib/jellyfin-ffmpeg/ffmpeg`，RGA 滤镜异步深度 1，debugfs 已确认 RGA3 工作。
- 主视频 `h264_rkmpp`：Constrained Baseline / Level 4.2，无 B 帧，目标 6M、上限 8M、缓冲 1M；GOP 30，60fps 时每约 0.5 秒出现 IDR，携带 SPS/PPS。
- 同一次采集分出 5fps `mjpeg_rkmpp`，原子更新 `/run/kvm-mcp/frame.jpg`，用于 MCP 和 JPEG 兼容画面。
- Python 仅解析已编码 H.264，再经 aiortc Packet 路径封装 RTP/SRTP；主视频不在服务器解码或软件重编码。
- 浏览器直接播放 video，避免逐帧 JPEG 的 Blob、ImageBitmap、canvas 绘制。客户端是否使用硬件解码取决于浏览器及系统。
- V4L2 mmap 原始帧上传仍存在 CPU 拷贝；不宣称整个链路零拷贝。

## 连接和恢复

SDP 协商沿用网页登录会话并检查 Origin，最多四个视频连接。仅发送视频，不请求摄像头或麦克风。会话过期、连接失败或 15 秒无法连接时清理资源。

每个查看者队列最多四帧；队列溢出后等待下一关键帧。新连接也等待关键帧，避免残缺预测链。丢包可通过 RTP 重传与周期性 IDR 恢复。当前编码码率固定，不随每个接收端反馈动态调整。

采用局域网 host ICE candidates，无公共 STUN/TURN。HTTP 8080 用于页面/信令，视频使用动态 UDP 端口。跨子网、防火墙、VPN 或公网需要相应连通性与 TLS/认证部署。仅转发 HTTP 端口的 SSH 隧道不能独立承载 WebRTC UDP，可使用 JPEG 兼容模式。

原有输入互斥、断线释放、明确交还 MCP 均保留。切换视频模式不改变输入控制权。

## 实测

`uv run python scripts/smoke_rtc.py --seconds 10` 执行真实 HTTP 登录、WebRTC 协商、UDP/SRTP 接收和逐帧解码：

| 指标 | 结果 |
| --- | --- |
| 输入 | HDMI 1920×1080 / 60Hz |
| 接收端解码 | 613 帧 / 10.159 秒，稳定阶段约 60.2fps |
| RTP | 收到 7,666 包，丢包 0 |
| 编码码率采样 | 约 6.99 Mbit/s |
| offer 到首个解码帧 | 0.984 秒，含连接建立与等待关键帧 |
| JPEG 兼容路径 | 20 帧 / 3.714 秒，约 5fps |
| MCP 截图 | 共享帧正常缩小至 640×360 |

证据在本机 `artifacts/webrtc-live-metrics.json`、`artifacts/webrtc-live-frame.jpg`。独立编码测试还验证了 180 帧/3秒、无 B 帧。临时合成源服务已关闭，正式服务始终使用真实 HDMI。

首帧时间、帧率和 RTP 指标不等于鼠标操作到屏幕的端到端延迟。接收端为 aiortc/PyAV；实际浏览器渲染与操作体验仍待验收。此前内置浏览器访问局域网被 `ERR_BLOCKED_BY_CLIENT` 拦截。

## 配置与回退

编辑远端 `/etc/kvm-mcp.env`，修改后执行 `systemctl restart kvm-web`：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `KVM_TRANSPORT` | `webrtc` | vendor 默认 H.264；`jpeg` 恢复完整帧率 JPEG |
| `KVM_H264_BITRATE` | `6000000` | 目标 bit/s，范围 500000..20000000；上限设为目标 4/3 |
| `KVM_MAX_FPS` | `60` | 帧率上限，不补帧 |
| `KVM_FFMPEG` | `/usr/lib/jellyfin-ffmpeg/ffmpeg` | 包含 MPP/RGA 的 FFmpeg |

网页“自动 · WebRTC”使用主视频；“兼容 · JPEG”使用 5fps 预览。WebRTC 只有实际出帧后才替换 JPEG；失败后保持兼容画面，手动重新选择“自动 · WebRTC”可重试。SSH 本地转发地址默认使用 JPEG，并按约 5Mbps 图像负载预算节流。刷新页面加载新版控制台。详见 [SSH 与远程画面](../REMOTE_VIEWING.md)。

部署前代码备份：远端 `/opt/kvm-mcp/backups/pre-webrtc.tar.gz`。一般只需设置 `KVM_TRANSPORT=jpeg`，无需恢复代码即可回退传输。主线内核的 GStreamer JPEG 路径仍保留。

## 检查

浏览器侧出现回退时，展开“连接与操作说明”查看诊断：

- `connection` / `ice` 未连接：尚未建立 UDP 媒体通道，不能据此判断 H.264 解码失败。
- 已连接但 `bytesReceived=0`：没有收到视频数据。
- 已收到数据但 `framesDecoded=0`：检查协商到的 `codecParameters` 和浏览器解码能力。
- `framesDecoded` 持续增加但画面不出现：检查播放、布局及渲染状态。
- `decoder` / `hardwareDecoder` 是浏览器可选统计；缺失不等于软件解码，也不能由服务端的 MPP 状态推断。

页面现在同时使用解码计数、loadeddata/playing 和视频帧回调判定播放，避免仅凭绘制回调误判失败。失败或首次播放时向本机服务报告上述诊断字段及浏览器类型，最近 20 条保存在服务内存中；登录后可读取 `/api/rtc/diagnostics`，日志可用 `journalctl -u kvm-web` 查看。不保存原始 SDP、ICE 密码或画面。

```sh
uv sync --frozen
uv run python -m unittest discover -s tests -t . -q
node --test tests/app/test_frontend.cjs
uv run python scripts/smoke_rtc.py --seconds 10
uv run python scripts/smoke_web.py --origin http://192.168.50.152:8080 --frames 20 --live
uv run python scripts/smoke_mcp.py
```

测试覆盖 H.264 协商（包括 VP8 优先的 offer）、数据包不重编码、队列溢出后的关键帧恢复、编码器重启、连接释放、鉴权与 Origin、视频坐标和输入互斥。
