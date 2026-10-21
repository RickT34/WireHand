# 架构与扩展约定

## 两个业务部分

```text
src/kvm_mcp/
├── app/                        MCP 与网页应用
│   ├── mcp.py                  MCP 工具工厂
│   ├── web.py                  HTTP、认证、WebSocket、应用生命周期
│   ├── control.py              浏览器输入状态与 DOM 键名映射
│   ├── ownership.py            人工接管与 MCP 输入仲裁
│   ├── rtc.py                  H.264 → WebRTC，连接与队列管理
│   ├── vision/                 候选检测、快照分页、放大标记、ID 点击与离线检查
│   └── static/                 网页资源，随 wheel 安装
├── platforms/
│   └── rock5b_plus/            ROCK 5B+ 硬件适配
│       ├── backend.py          实现 Backend 和语义输入会话
│       ├── hid.py              HID 报告、configfs 发现与设备写入
│       ├── device.py           HDMI RX 设备发现
│       ├── snapshot.py         共享 JPEG 读取与独立截图
│       ├── video.py            V4L2、GStreamer、MPP/RGA 视频管线
│       ├── gadget.py           USB gadget 初始化与停止
│       ├── hdmi.py             EDID 配置与恢复
│       └── edid/               板卡 EDID 资源
├── contracts.py                两部分共同遵循的 Protocol
├── media.py                    编码帧广播，无 aiortc/PyAV 依赖
├── runtime.py                  通用跨进程文件锁
└── bootstrap.py                配置入口、后端加载、组装应用
```

依赖方向：`app → contracts/media/runtime ← platforms`。只有组装入口知道具体平台。依赖边界由导入检查和独立 Python 进程测试验证。

本轮采用一个可安装 Python 包、两个明确的代码子包；没有增加硬件 RPC 服务或网络协议。应用与适配器在同一进程中调用，MCP 与网页可分别启动。未来若需要把硬件放到另一台主机，可新增实现同一接口的远程适配器。

## 接口

| 接口 | 责任与返回值 |
| --- | --- |
| `Backend.name` | 平台标识，用于状态展示 |
| `Backend.status()` | 平台状态与诊断，包括 `platform`、`input`、`video`；不包含人工控制策略 |
| `Backend.input_state()` | `{"connected": bool, "detail": str}`；不要求应用认识 USB 状态码 |
| `Backend.screenshot(max_width)` | 新鲜 JPEG 字节，按比例缩小至不超过指定宽度；范围 320..3840 |
| `Backend.open_input()` | 上下文管理器，打开 `InputSession`，退出时释放设备资源 |
| `Backend.create_video()` | 创建 `VideoSource`；构造时不启动设备 |
| `InputSession.keys(keys)` | 设置当前按住的逻辑键；空列表释放 |
| `InputSession.pointer(x,y,buttons,wheel)` | 设置绝对位置、持续按钮状态和相对滚轮；坐标为 0..1，按钮位为左 1/右 2/中 4 |
| `InputSession.press_key/type_text/click` | 一次性动作；无效输入不发出任何事件，动作结束或失败均尝试释放 |
| `InputSession.release_all()` | 尝试释放键盘和指针按钮，再汇总报错；保留指针位置 |
| `VideoSource.run()` | 持续采集、恢复信号；取消时清理子进程与占用的设备 |
| `VideoSource.next_frame(after)` | 等待更新且新鲜的 `(sequence, jpeg_bytes)`，慢客户端跳帧 |
| `VideoSource.status()` | 至少包含 `signal`、`error`，有画面时提供 `width/height`；帧率等诊断可选 |
| `VideoSource.encoded` | `EncodedHub` 或 `None`；后者表示只支持 JPEG |

键名采用小写逻辑键，例如 `ctrl`、`a`、`enter`、`f2`。`type_text` 接受 1..1000 个 US 布局 ASCII 字符；`press_key` 的保持时间为 10..1000ms；滚轮为 -127..127。平台负责将这些语义转换为设备协议，并在写入前完成整条请求校验。浏览器 DOM 键名到逻辑键名的映射属于应用。

H.264 平台在同一个 asyncio 事件循环中调用 `encoded.publish(data, pts, keyframe)`。`data` 是 Annex-B access unit，`pts` 为单调递增的 90kHz 时间戳。编码不得使用 B 帧，关键帧应包含解码所需 SPS/PPS；编码器重启时调用 `encoded.reset()`。消费者使用最多四帧的队列，溢出后丢弃预测链并等待下一个关键帧。此接口不要求平台构造 aiortc 对象。

同步平台方法允许阻塞；HTTP 状态查询和输入由 `asyncio.to_thread` 调用。视频生命周期与订阅运行在事件循环。平台模块导入时不访问设备。

## 控制权与运行目录

同一目标的所有进程共用 `KVM_RUNTIME`。`runtime.py` 只提供互斥；`app/ownership.py` 在互斥内检查 `human-control` 标记，平台不知道“人工”或“MCP”。

1. MCP 输入获取 `input.lock`，检查接管标记，再打开输入会话。结束后推进 `input-revision`，使其他进程中的旧视觉快照失效。
2. 浏览器接管获取同一把锁，打开输入会话，创建标记并释放残留按键；持锁直至断开或交还。获取和释放人工会话均推进 revision。
3. 正常断开、七秒无消息超时或输入错误会释放键鼠和会话，在持锁期间删除接管标记，然后交还输入锁。释放失败仍结束接管，错误会被报告或记录。
4. 明确交还与断开使用同一条清理路径。进程被强制终止时，重启会在获取输入锁后尝试释放遗留按键并删除旧标记；获取不到锁时不打断其他活动会话。

ROCK 适配器额外使用 `video.lock` 协调长时间采集与独立截图，并通过原子替换的 `frame.jpg` 共享最近画面。这个缓存是适配器实现细节。视频退出持锁完成进程回收和缓存清理；未取得锁的实例不删除其他进程的缓存。

## 新增平台

在 `platforms/<name>/` 实现接受 `runtime: Path` 的工厂或类，遵循 `contracts.py`；初始化/驱动脚本也放在该平台目录。通过工厂路径启动，无需修改 MCP、网页或其控制器：

```sh
kvm-web --backend my_package.my_platform:create_backend
kvm-mcp --backend my_package.my_platform:create_backend
```

先以 `tests/fakes.py` 为应用接口参考，再添加该平台自己的协议与资源清理测试。应用测试只依赖这个替代后端，不导入 ROCK 硬件实现；平台测试验证 HID、EDID、视频解析与资源生命周期。

## 本次接口变更

旧的 `server.py`、`web_server.py` 等根目录入口已移除。系统服务与 MCP 配置必须改用安装后的命令；静态文件与 EDID 随包分发。HTTP 状态使用通用 `input.connected/detail`，MCP 状态使用 `platform/input/video/manual_control`。没有旧字段、旧 import 或旧脚本兼容层。
