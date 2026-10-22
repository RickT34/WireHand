# 文档导航

本项目通过视频与键鼠接口控制电子设备，被控端无需安装控制软件。连接实体设备从 [ROCK 5B+ 部署](rock5b-plus/README.md) 开始；先体验软件功能可从 [项目 README](../README.md) 的 Docker 快速开始进入。

| 我想做什么 | 阅读文档 |
| --- | --- |
| 启动一个持久化 Linux 桌面，配置访问与备份 | [Docker 桌面](DOCKER_DESKTOP.md) |
| 接管设备、录制、上传和执行脚本 | [网页控制台与脚本库](WEB_CONSOLE.md) |
| 编写 Python 自动化、使用图像目标与录制 API | [Python SDK](AUTOMATION.md) |
| 把设备交给 AI agent 操作 | [MCP 接入](MCP.md) |
| 调整密码、端口、后端和运行目录 | [配置参考](CONFIGURATION.md) |
| 无需安装被控端软件，通过 HDMI 与 USB 控制电子设备 | [ROCK 5B+ 部署](rock5b-plus/README.md) |
| 配置 SSH 转发、HTTPS 或排查视频 | [远程访问与视频](REMOTE_VIEWING.md) |
| 理解视觉定位并复现真实截图上的编号点击 | [视觉定位与真实设备示例](VISUAL_TARGETING.md) |
| 修改代码、运行测试或构建安装包 | [贡献与本地开发](../CONTRIBUTING.md) |
| 新增设备后端或调整模块边界 | [架构与扩展](ARCHITECTURE.md) |

## API 参考

启动网页后访问 `/docs/`，或点击右上角“SDK 文档”。页面由 pdoc 根据当前安装的代码生成，包含函数签名、参数、示例和搜索。

离线生成并查看：

```bash
uv sync --frozen
uv run wirehand-docs --output artifacts/sdk-docs
python3 -m http.server 8765 --bind 127.0.0.1 --directory artifacts/sdk-docs
```

打开 <http://127.0.0.1:8765/>。

## 历史资料

[历史文档目录](history/README.md) 保留早期原型、特定版本驱动部署和验收记录。当前安装命令以本页列出的使用指南为准；历史性能数据对应各文档注明的版本与测试条件。
