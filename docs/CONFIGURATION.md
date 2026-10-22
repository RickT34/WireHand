# 配置参考

[返回文档导航](README.md)

应用通过环境变量读取配置。CLI 的 `--backend`、`--runtime` 参数优先于同名环境变量；SDK 的显式参数优先于环境变量。

## 通用配置

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `KVM_BACKEND` | 必填，无默认 | `docker-desktop`、`rock5b-plus`、`image` 或 `module:factory` |
| `KVM_RUNTIME` | `/run/kvm-mcp` | 输入锁、控制权和平台运行状态；同一设备的各进程共用 |
| `KVM_TOKEN` | 网页必填 | 登录密码，至少 20 个字符 |
| `KVM_BIND` | `0.0.0.0` | 网页监听地址 |
| `KVM_PORT` | `8080` | 网页监听端口 |
| `KVM_HOSTS` | `localhost,127.0.0.1` | 允许访问的主机名/IP，不含端口，逗号分隔 |
| `KVM_RECORDINGS` | `~/.local/share/kvm-mcp/recordings` | 网页脚本库持久目录 |
| `KVM_TRUSTED_PROXIES` | 空 | 信任转发头的代理 IP/CIDR；配置方法见 [HTTPS 反代](REMOTE_VIEWING.md) |
| `KVM_SCREENSHOT` | `image` 后端必填 | 静态图片文件路径 |

`/run/kvm-mcp` 需要服务用户有写权限。普通用户在无硬件开发时可用 `/tmp/kvm-dev`；不要让同一设备的不同接口各自使用不同目录。

## Docker 配置

`desktopctl init` 创建 `deploy/docker-desktop/.env`。其中的配置由 Compose 和容器启动脚本读取：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `KVM_TOKEN` | `init` 随机生成 | 网页密码 |
| `KVM_DESKTOP_BIND` | `127.0.0.1` | 宿主机发布端口的监听地址 |
| `KVM_DESKTOP_PORT` | `18810` | 宿主机端口，转发到容器内 8080 |
| `KVM_DESKTOP_RESOLUTION` | `1280x800` | 虚拟桌面分辨率，宽 640–3840、高 480–2160 |
| `KVM_HOSTS` | `localhost,127.0.0.1` | 浏览器访问的主机名/IP |
| `KVM_TRUSTED_PROXIES` | 空 | 按实际代理网络配置 |

修改 `.env` 后执行 `./deploy/docker-desktop/desktopctl up`。容器内的后端、显示器、运行目录和脚本库目录已经设置；其中 `KVM_RECORDINGS=/var/lib/kvm-mcp/recordings` 对应持久化的数据卷。

`.env` 不会被普通 `wirehand-web` 进程自动读取。源码运行时需要 `export` 环境变量；systemd 使用服务中的 `EnvironmentFile`。

## 本地网页启动示例

以下示例用于已经配置好驱动和设备权限的 ROCK 5B+，在板子上运行：

```bash
export KVM_BACKEND=rock5b-plus
export KVM_BIND=127.0.0.1
export KVM_HOSTS=localhost,127.0.0.1
# 输入自己生成并保存的密码：
read -r -s -p 'Web password: ' KVM_TOKEN
export KVM_TOKEN
uv run wirehand-web
```

Docker 用户直接使用 `desktopctl`。ROCK 的设备节点、编码器和帧率变量见 [平台配置](rock5b-plus/README.md#平台配置)。
