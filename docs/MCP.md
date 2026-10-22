# MCP 接入

[返回文档导航](README.md)

MCP 服务使用 **stdio** 传输。客户端启动服务进程，通过标准输入输出调用工具；网页是独立的 HTTP 服务，不提供 `/mcp` HTTP 端点。

## Docker 桌面

先按 [Docker 桌面指南](DOCKER_DESKTOP.md) 启动桌面，再向支持 stdio 的 MCP 客户端添加：

```json
{
  "mcpServers": {
    "desktop": {
      "command": "/absolute/path/to/wirehand/deploy/docker-desktop/desktopctl",
      "args": ["mcp"]
    }
  }
}
```

替换仓库绝对路径。`desktopctl mcp` 在已运行的容器内，以 `agent` 用户启动服务；后端、显示器和运行目录与网页保持一致。客户端需要在能够执行该脚本并访问 Docker 的主机上运行。退出 MCP 会话后，桌面继续运行。

## ROCK 5B+ / SSH

完成 [板端部署](rock5b-plus/README.md)，配置免交互 SSH 登录后使用：

```json
{
  "mcpServers": {
    "rock-kvm": {
      "command": "ssh",
      "args": [
        "-T", "-o", "BatchMode=yes", "root@rock-kvm",
        "/opt/kvm-mcp/.venv/bin/wirehand-mcp",
        "--backend", "rock5b-plus",
        "--runtime", "/run/kvm-mcp"
      ]
    }
  }
}
```

`rock-kvm` 是示例 SSH 主机名，替换为自己的地址或别名。上述服务模板以 root 运行；若自行配置其他服务用户，应为网页、MCP 和 SDK 配置同一用户及相应设备权限。

SSH 不分配 TTY，远端 shell 启动脚本也应避免向 stdout 输出欢迎信息。网页的 `EnvironmentFile` 不会自动传给 SSH 会话，后端与运行目录在参数中明确指定。

## 工具与常用流程

| 工具 | 用途 |
| --- | --- |
| `status` | 查询视频和输入连接 |
| `screenshot(max_width)` | 获取 JPEG，宽度范围 320–3840 |
| `inspect_screen(...)` | 获取带编号的总览与局部图 |
| `inspect_region(...)` | 放大区域、分页或使用网格定位 |
| `click_target(snapshot_id, target_id, button)` | 校验当前画面后点击编号目标 |
| `press_key(keys, hold_ms)` | 按下并释放组合键 |
| `type_text(text)` | 输入 1–1000 个 US 布局 ASCII 字符 |
| `mouse(x, y, button, wheel)` | 使用 0–1 屏幕相对坐标移动、点击或滚动 |
| `release_all` | 释放按住的键鼠 |

建议先 `status` 和 `inspect_screen`，从返回的图像和元数据中选择目标，再调用 `click_target`。画面变化或发送输入后重新观察；分页应复用同一个 `snapshot_id`。密集标记、网格定位与快照失效规则见 [视觉定位](VISUAL_TARGETING.md)。

## 控制权

网页接管期间，全部 MCP 工具返回占用错误。网页脚本运行期间同样预留输入，避免其他自动化会话插入操作。结束接管或停止脚本后，重新观察再继续执行。

控制权由共同的 `KVM_RUNTIME` 目录协调。连接同一设备的各进程应使用同一目录；不同设备应使用不同目录。

## 无设备调试

在安装源码的环境中，可以用一张静态图片检查工具返回格式：

```bash
KVM_SCREENSHOT=/absolute/path/to/screen.png uv run wirehand-mcp \
  --backend image --runtime /tmp/kvm-image
```

图片后端支持观察与区域细分，输入请求会返回只读错误。图像候选的 ID 属于当前 MCP 会话，不能从离线 HTML 检查页复制到其他会话使用。

## 常见问题

| 现象 | 检查位置 |
| --- | --- |
| 客户端无法启动服务 | 仓库或可执行文件的绝对路径、Docker 是否运行、SSH 是否能免交互连接 |
| 一直提示接管或脚本占用 | 网页的当前模式和运行面板；正常结束控制会话或任务 |
| 工具找不到设备 | 后端所在环境、板端驱动、容器内 `DISPLAY` 与输入权限 |
| `snapshot_id` 过期或画面已变化 | 重新调用 `inspect_screen` |
| 输入与网页互相干扰 | 是否使用相同服务用户和 `KVM_RUNTIME` |
