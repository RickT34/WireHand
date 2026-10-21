# Docker 持久桌面

`docker-desktop` 提供 Debian 13 + Xfce + Xvfb 桌面，预装 Firefox ESR、终端、Thunar 文件管理器、Mousepad 和中文字体。通过项目原有网页、MCP 和 Python API 操作，使用同一套接管互斥、视觉定位和录制功能。

## 启动与访问

需要 Linux Docker Engine、Docker Compose 和宿主机 Python 3。仓库根目录执行：

```sh
./desktopctl init
./desktopctl up
./desktopctl status
```

打开 <http://127.0.0.1:18810/>。密码是 `deploy/docker-desktop/.env` 中的 `KVM_TOKEN`；`init` 随机生成并以 `0600` 权限保存，重复执行不会更换密码。该文件被 Git 和镜像构建上下文排除。

默认分辨率 `1280x800`。需要修改时编辑 `.env` 的 `KVM_DESKTOP_RESOLUTION`，再运行 `./desktopctl up`，范围为宽 640..3840、高 480..2160。网页只读观看，点击“接管控制”后可操作；此时 MCP 输入会被服务端拒绝，交还后恢复。

默认只绑定宿主机回环地址。远程使用 SSH 转发：

```sh
ssh -N -L 18810:127.0.0.1:18810 user@server
```

如需局域网直接访问，将 `.env` 的 `KVM_DESKTOP_BIND` 改为服务器的局域网地址，并在 `KVM_HOSTS` 追加实际访问的 IP/域名，保留 `localhost,127.0.0.1` 供本地健康检查使用。重新执行 `./desktopctl up`。需要 HTTPS 时使用现有 [Nginx 部署说明](REMOTE_VIEWING.md)。

## 数据保存与备份

默认 Compose 项目名固定为 `kvm-desktop`，对应三个命名卷：

| Docker 卷 | 容器内路径 | 内容 |
| --- | --- | --- |
| `kvm-desktop_home` | `/home/agent` | 桌面文件、下载、Firefox 配置与登录状态、应用设置 |
| `kvm-desktop_workspace` | `/workspace` | 工作文件，桌面用户主目录的 `Workspace` 链接指向这里 |
| `kvm-desktop_state` | `/var/lib/kvm-mcp` | 网页录制、截图素材及重放脚本 |

`up`（包括重新构建镜像和替换容器）、`restart`、`down` 均保留这些卷。`down` 只停止并删除本项目容器和网络。更换项目名会使用另一组卷。不要给 Compose `down` 添加 `-v`，也不要删除这些卷。

运行中的进程、未保存的编辑器内容、容器系统目录中的临时安装不属于持久数据；额外系统软件应写进 Dockerfile。运行目录 `/run/kvm-mcp` 是锁和控制权状态，不需要备份。现有 Deskling 的卷与新桌面互相独立，不自动迁移。

备份前退出应用并停止容器，使浏览器数据库等完成落盘。以下命令备份默认项目的三个卷，使用本项目已构建的镜像，无需再下载工具镜像：

```sh
./desktopctl down
umask 077
mkdir -p backups
for volume in home workspace state; do
  docker run --rm --user 0 --entrypoint tar \
    -v "kvm-desktop_${volume}:/data:ro" \
    -v "$PWD/backups:/backup" kvm-mcp/desktop:local \
    -C /data -czf "/backup/${volume}.tar.gz" .
done
./desktopctl up
```

另行安全保存 `.env`。恢复到新主机时，先构建镜像、用 `docker volume create kvm-desktop_home` 等建立空卷，再用同样挂载方式去掉 `:ro` 并执行 `tar -C /data -xzf /backup/home.tar.gz`，分别恢复三个卷，最后 `up`。

## MCP 与 Python

桌面后端在容器内连接隔离的 X11 显示器。宿主机通过 `docker compose exec` 运行 MCP，无需挂载 Docker socket 或宿主机 X11 socket，也不需要特权容器。

MCP 客户端的 stdio 配置示例（路径替换为实际仓库路径）：

```json
{
  "mcpServers": {
    "desktop": {
      "command": "/absolute/path/to/kvm_mcp/desktopctl",
      "args": ["mcp"]
    }
  }
}
```

`./desktopctl mcp` 使用非 TTY 的 stdin/stdout 和 `agent` 用户，自动复用容器内 `KVM_BACKEND=docker-desktop`、`KVM_RUNTIME=/run/kvm-mcp`。退出 MCP 不会停止桌面。宿主机直接执行 `connect(backend='docker-desktop')` 不会自动启动或连接 Docker；Python 脚本应在容器内执行：

```sh
./desktopctl shell
python - <<'PY'
from kvm import connect
with connect() as desktop:
    print(desktop.size())
    desktop.click(200, 200)
    desktop.write('hello')
PY
```

将脚本保存到 `/workspace` 或 `/home/agent` 即可持久化。网页、MCP 与 SDK 共用显示器和运行目录；不要为同一桌面另设不同的 `KVM_RUNTIME`。

## 运行与验证

```sh
./desktopctl logs       # 只查看本项目日志
./desktopctl restart    # 重启桌面，保留已保存数据
./desktopctl down       # 停止并删除容器，保留数据卷
./desktopctl up         # 构建/更新并等待桌面健康

uv run python -m unittest tests.platforms.test_docker_desktop -v
uv run python scripts/smoke_desktop.py
# 在无人使用桌面时，额外验证替换容器后数据依旧存在：
uv run python scripts/smoke_desktop.py --recreate
```

实测脚本会在桌面创建临时测试窗口并发送输入，检查真实 X11 键鼠与截图、HTTP/JPEG WebSocket、stdio MCP 和人工接管互斥。`--recreate` 会在三个卷写入唯一测试文件、替换本项目容器、比对文件内容并删除本次测试文件；未保存的应用状态会丢失。

当前预览是最高 5fps、宽度上限 1280 的 JPEG，未接入 H.264、音频或剪贴板通道。输入沿用项目 US 键盘布局 ASCII 约定；中文字体可显示中文，中文输入法需另行配置。Dockerfile 使用 Debian 多架构基础镜像；本次已验证 ARM64，其他架构尚未实测。

### 2026-10-21 验证记录

- 环境：本机 Linux aarch64，Docker Engine 29.9.1、Compose 5.6.0；镜像从 Debian trixie-slim 成功构建，容器健康检查通过。
- 普通主机测试：128 项 Python 测试（仅真实 Xvfb 项在宿主机跳过）、22 项前端测试通过；wheel 和源码归档构建成功，并确认部署文件随源码分发、私有 `.env` 未进入归档。
- 容器测试：10 项桌面测试全部通过，实际捕获红色测试窗口、验证键盘修饰键和文本、指针坐标与按钮、会话退出和遗留按键释放。
- `uv run python scripts/smoke_desktop.py --recreate` 通过：真实 stdio MCP 截图/按键、HTTP 登录、JPEG WebSocket、人工接管拒绝 MCP 输入、交还恢复、录制 ZIP 下载；替换容器后核对三个卷的文件及录制数据仍在。
- 实际启动 Firefox ESR 并检查窗口和桌面截图。空闲桌面一次采样约 180 MiB 内存，浏览器打开后的占用另计；这不是长期资源上限。

本地验证日志和截图保存于仓库的 `artifacts/docker-desktop/`（不提交到 Git）。本次未验证真实浏览器客户端对网页控制台的交互体验、音频或 H.264。
