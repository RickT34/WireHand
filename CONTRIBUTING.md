# 贡献与本地开发

感谢帮助改进 WireHand · 接手。提交问题时，请说明使用的平台、复现步骤、预期结果和实际结果。涉及画面或输入问题时，附上分辨率、视频模式和相关错误；日志中的访问密码、Cookie 和私人屏幕内容应先移除。

## 安装开发环境

需要 Linux、Python 3.11+ 和 uv。前端测试另需支持 `node --test` 的 Node.js。仓库根目录执行：

```bash
uv sync --frozen
```

只使用标准 Python 工具时：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

项目只有一个安装包，Python 导入名为 `wirehand`。`requirements.txt` 是依赖导出；通过项目安装才能获得命令行入口、静态资源和 EDID。

## 不连接硬件开发

录制与重放演示：

```bash
uv run python examples/record_replay_demo.py --output artifacts/automation/demo
```

输出目录必须尚不存在。演示将按钮移动后重新定位，并输出录制数据、脚本、图像和结果。

模拟网页：

```bash
uv run python examples/web_recording_demo.py
```

访问 <http://127.0.0.1:8767/>，密码为演示专用的 `recording-demo-local-token-2026`。它使用合成画面与模拟输入；真实应用交互请使用 [Docker 桌面](docs/DOCKER_DESKTOP.md)。

真实设备截图的离线标记与模板示例：

```bash
uv run python examples/visual_targeting_demo.py docs/images/visual-real-screen.jpg \
  --region C09 --target T234 --output artifacts/vision/real-example
```

输出目录需为空；程序使用保存的真实帧和内存输入后端，不发送设备输入。详见 [视觉定位](docs/VISUAL_TARGETING.md)。

其他入口：

```bash
uv run wirehand-inspect /path/to/screen.png --output artifacts/vision/demo
uv run python examples/video_transport_demo.py
uv run wirehand-docs --output artifacts/sdk-docs
```

视觉导出的查看方式见 [视觉定位](docs/VISUAL_TARGETING.md)，视频演示的密码与验收方式见 [远程视频](docs/REMOTE_VIEWING.md)。

## 验证与构建

```bash
uv run python -m unittest discover -s tests -t . -q
node --test tests/interfaces/test_*.cjs
uv build
```

默认测试使用替代设备后端，包含真实 stdio MCP 和 HTTP/WebSocket 测试。真实 Xvfb 测试需要隔离容器并显式启用；Docker 验收入口见 [Docker 桌面](docs/DOCKER_DESKTOP.md#运行与验证)。

修改依赖后同步导出：

```bash
uv lock
uv export --frozen --no-hashes --no-dev --no-emit-project -o requirements.txt
```

## 代码与文档放在哪里

- `src/wirehand/core/`：通用设备协议、媒体接口和控制权。
- `src/wirehand/vision/`：图像候选、匹配与渲染。
- `src/wirehand/automation/`：Python API、录制、脚本库与执行。
- `src/wirehand/platforms/`：各平台的截图、输入和视频实现。
- `src/wirehand/interfaces/`：网页与 MCP 接口。
- `src/wirehand/cli/`、`factory.py`：启动配置与实例组装。

新增平台使用 `module:factory` 接入；依赖边界与协议要求见 [架构说明](docs/ARCHITECTURE.md)。API 参数与示例写在 docstring 中，pdoc 据此生成参考页；用户工作流放在 `docs/`。README 的[工作原理图](docs/images/how-it-works.svg)是可直接编辑的 SVG，修改连线时需保持画面与键鼠输入方向正确。

提交改动时，说明解决的问题、验证方式和涉及的平台。与硬件有关的变更应分别注明本地测试与实机验证结果。
