# 远程视频、SSH 与 Nginx SSL

网页默认使用 **H.264 over WebSocket**。HTTPS 页面自动使用 WSS；视频、键鼠、状态和录制下载均走网页的同一个域名与端口，不依赖 UDP、STUN 或 TURN。平台适配器输出 H.264 Annex-B，应用层负责传输与播放。

## 浏览器播放与延迟策略

- “自动 · 低延迟 H.264”：优先 WebCodecs 解码后直接绘制 canvas。WebCodecs 不可用或不支持编码配置时自动转为 MSE。
- “兼容 · H.264”：服务端将相同编码帧重新封装为 fMP4，浏览器 MSE 播放，不进行二次编码。封装有约一帧等待；播放器控制缓冲并追赶最新画面。
- “直连 · WebRTC”：保留原来的 host ICE / UDP 路径，适合局域网或已有可用媒体网络的场景。Nginx 普通 HTTP 反代和 `ssh -L` 不转发这条 UDP 媒体链路。
- “备用 · JPEG”：只拉取最新帧，最高 5fps、约 5Mbps 图像负载预算。

首次连接、切换和恢复时保留 JPEG，H.264 实际出帧后才切换。H.264 播放失败会尝试 MSE 或有限次数重连，随后保持 JPEG；选择视频模式可手动重试。刷新页面会重新尝试默认模式。WebCodecs 通常要求 HTTPS 或本机 localhost 安全上下文；普通远程 HTTP 可以使用浏览器支持的 MSE。

WS/WSS 每个观看者有独立背压：

- 待发送队列最多两帧；队列溢出或帧等待超过 150ms 时丢弃旧预测链，等待新 IDR，避免发送损坏的 P 帧链。
- 最多八个未确认消息，常规发送窗口为 128KiB。超过窗口大小的单帧仅在窗口清空后发送，单帧硬上限 1MiB。
- WebCodecs 在绘制后 ACK，解码队列达到两帧时重同步。MSE 在分片追加完成后 ACK，并移除旧播放缓冲；落后超过 250ms 时向缓冲末尾跳转。
- 1.5 秒没有确认、发送阻塞或连接失效时关闭媒体连接。观看者最多四个。键鼠使用独立 WebSocket，但仍受底层网络带宽影响。

这些是应用层限制，不是端到端延迟保证。TCP 丢包仍会触发重传等待，SSH 隧道也可能把视频与输入放在同一个 TCP 连接里。画面复杂度、RTT、编码器和浏览器会影响实际反馈速度。

## ROCK 默认编码配置

默认按原比例缩放到不超过 1280×720、最高 60fps，目标码率 2.5Mbps，无 B 帧，关键帧间隔约半秒。宽高按硬件要求向下对齐到 16×8。码率上限配置为目标的 4/3；真实硬件是否严格遵守需实机确认。

```ini
KVM_TRANSPORT=h264
KVM_MAX_WIDTH=1280
KVM_MAX_HEIGHT=720
KVM_MAX_FPS=60
KVM_H264_BITRATE=2500000
```

更窄带宽可降低宽高、码率或帧率。只有 JPEG 的平台仍可运行网页；不会获得 H.264 传输。共享 JPEG 预览、网页录制截图和 MCP 共享截图也使用当前采集输出分辨率；以上上限不改变目标机 HDMI 模式。

## Nginx HTTPS/WSS

完整模板：[deploy/nginx/kvm.conf](../deploy/nginx/kvm.conf)。将其放入 Nginx 的 `http {}` 包含目录，替换域名、证书文件和 upstream。当前部署要求独立域名根路径，不支持挂在 `/kvm/` 子路径。

同机 Nginx 的应用环境变量：

```ini
KVM_BIND=127.0.0.1
KVM_PORT=8080
KVM_HOSTS=kvm.example.com,localhost,127.0.0.1
KVM_TRUSTED_PROXIES=127.0.0.1
```

继续使用原有 `KVM_TOKEN`。`KVM_HOSTS` 是浏览器访问的域名/IP，不含端口。`KVM_TRUSTED_PROXIES` 是应用实际看到的代理源 IP/CIDR，默认空，不接受 `*`。如果代理位于另一台机器，将应用绑定到可达内网地址，并只填写那台代理的地址；同时限制应用端口只对需要的内网来源开放。

模板显式转发 WebSocket Upgrade、原始 Host 与 `X-Forwarded-Proto`，关闭代理缓冲，并在入口代理覆盖客户端自带的转发头。应用仅信任配置的代理地址，因此在 TLS 终止后仍能校验 HTTPS Origin、建立 WSS 会话并设置 Secure 登录 Cookie。不要把外部 Host 改成 upstream 地址。

替换配置后检查并加载：

```sh
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl restart kvm-web
```

访问 `https://kvm.example.com/` 并刷新页面。只需为此服务开放 HTTPS 端口；此模式的视频不需要额外 UDP 端口。模板不会申请证书或开放防火墙。

## SSH 本地转发

```sh
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 r5p
```

打开 `http://127.0.0.1:18080/`，默认 H.264/WS 直接通过隧道。应用的 `KVM_HOSTS` 需包含 `127.0.0.1`。SSH 隧道未终止 HTTPS，不需要配置 `KVM_TRUSTED_PROXIES`。

## 本地调试与验收

无板卡时启动软件编码测试源：

```sh
uv sync --frozen
uv run python examples/video_transport_demo.py --image /path/to/screenshot.jpg
```

打开 `http://127.0.0.1:8800/`，密码在 `/tmp/kvm-h264-demo-token`。省略图像参数使用合成背景。图上叠加移动标记与帧计数；演示不会打开物理 HID。用 `--trusted-proxy 127.0.0.1` 可测试同机 Nginx TLS 终止，Nginx upstream 改为 `127.0.0.1:8800`。

协议探针会登录并实际解码视频：

```sh
uv run python scripts/smoke_h264.py --origin http://127.0.0.1:8800 --token-file /tmp/kvm-h264-demo-token
uv run python scripts/smoke_h264.py --origin http://127.0.0.1:8800 --token-file /tmp/kvm-h264-demo-token --format mp4
uv run python scripts/smoke_h264.py --origin https://kvm.example.com --token-file web-access.txt --seconds 10
uv run python scripts/smoke_h264.py --origin https://kvm.example.com --token-file web-access.txt --slow
```

`--slow` 故意不 ACK，验证发送窗口有界且连接超时关闭。自建测试 CA 使用 `--ca-file /path/to/ca.pem`，保留证书验证。`--bandwidth-mbps 10 --ack-delay-ms 80` 在客户端模拟消费速度与额外 ACK 延迟，不等同于网络层带宽整形或 WAN 测试。`--output artifacts/wss/result.json` 保存结果，不含密码或 Cookie。

Python 探针验证 Annex-B/fMP4 解码和认证；浏览器播放兼容性需在实际控制台中另外确认。前端与服务端回归：

```sh
uv run python -m unittest discover -s tests -t . -q
node --test tests/interfaces/test_*.cjs
```

## 本次验证范围（2026-10-21）

板子未开机，按要求仅本地测试。使用此前保存的屏幕截图叠加移动标记，软件编码为 1280×720 H.264；不是实时 HDMI 采集。

- 浏览器控制台分别验证 WebCodecs 与 MSE 出帧，保留录制栏和输入状态。
- 本地独立 Nginx 实例终止 TLS；探针显式信任临时证书，验证 HTTPS 登录、Secure Cookie、WSS Upgrade 和两种封装的实际解码。
- 5 秒 WSS 探针：Annex-B 解码约 60fps，负载约 2.24Mbps；fMP4 解码约 59fps，负载约 2.26Mbps。
- 10 秒测试加入 10Mbps 消费限速与 80ms ACK 延迟：解码约 60fps，负载约 2.40Mbps，最后 ACK 往返约 85ms。ACK 指标包含客户端处理/人工延迟，不能当作操作到画面的延迟。
- 无 ACK 客户端验证有界发送与超时断开；原始 JSON 与浏览器截图位于被 Git 忽略的 `artifacts/wss/`。

新编码参数尚未在 ROCK MPP 上运行，实际 WAN、显示器到浏览器的延迟、目标机键鼠反馈和不同浏览器仍待实机验收。2026-10-20 的 SSH/JPEG 诊断证据保存在 `artifacts/video-diagnosis/`，描述的是当时的旧播放策略。
