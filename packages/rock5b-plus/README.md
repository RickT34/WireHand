# kvm-rock5b-plus

ROCK 5B+ 的 HDMI/V4L2/MPP/RGA 与 USB gadget 适配器。依赖 `kvm-automation` 和 PyAV，不依赖 MCP、Starlette 或 aiortc。

从仓库安装：

```sh
pip install ./packages/automation ./packages/rock5b-plus
```

安装后注册 `rock5b-plus` 后端，以及 `rock5b-plus-gadget` 和 `rock5b-plus-hdmi` 命令。设备初始化、系统依赖和 systemd 配置见仓库 `docs/rock5b-plus/README.md`。EDID 文件随本包分发。
