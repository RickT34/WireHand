# 视觉定位

`kvm.vision` 负责图像候选检测与标记渲染，`kvm.automation.inspection.Inspector` 负责截图会话、分页和按 ID 点击，MCP 负责对 agent 暴露工具。平台仍只提供截图和语义输入，不需要知道候选检测、图像分页或标记布局。

## MCP 调用

```text
inspect_screen()
  → JSON 元数据 + 总览 PNG + 最多 3 张局部标记 PNG
  → snapshot_id、regions、targets、next_page

inspect_screen(snapshot_id="…", page=1)
  → 同一帧的后续区域，目标 ID 保持不变

inspect_region(snapshot_id="…", region_id="R02", mode="detect")
  → 在该区域重新检测，返回局部放大图

inspect_region(snapshot_id="…", region_id="FULL", mode="grid")
  → 4×4 网格，每格有目标 ID

inspect_region(snapshot_id="…", region_id="T023", mode="grid")
  → 继续细分选中的网格或候选框

click_target(snapshot_id="…", target_id="T055", button="left")
  → 验证新画面，解析原图坐标，调用平台输入
```

`page_size` 为每次返回的局部图数量，默认 3，范围 1..4；另带一张总览。每张局部图最多 18 个候选。总览只突出当前页的区域，JSON 列出全部区域；可以跳到任意区域或后续页。空白屏幕没有候选时仍返回总览，可使用 `FULL` 网格。

候选超过 180 个时，`inspect_screen` 改为返回一张 3×3 区域总览，`presentation="regions"`，区域 ID 为 `C01`..`C09`。agent 调用 `inspect_region(snapshot_id, "C09")` 查看该区域的具体编号；`inspect_region(snapshot_id, "FULL")` 仍能分页取得全部候选，没有丢弃候选。这个策略减少背景插画很复杂时遍历无关标记的成本。

元数据中的 `box` 是原截图的像素框，`point` 是实际点击中心。裁剪、缩放、编号位置均不影响鼠标坐标。编号只在对应 snapshot 中有效，工具会检查 ID，而不会把字符串当成坐标执行。

## 检测与展示

第一版使用本地 OpenCV，不下载模型，不调用云服务，也不使用被控系统的 DOM 或无障碍树。

- 边缘与形态学操作生成文字/图标候选，局部对比度补充渐变背景上的漏检。
- 重叠、包含关系去除重复轮廓；紧邻、基线一致的文字碎片尝试合并。
- 以空间位置、候选数量和大间隙递归分组；孤立目标不会把附近工具栏拉成大片空白。
- 保留上下文，局部最高放大 2.5 倍；在标记间检查碰撞，同时避开已有目标框和明显图像边缘。
- 顶部预留标签空间；放不下的编号进入侧栏，连线连接真实点击点。标签没有覆盖控件的必要。

检测接口是 `Detector.detect(image) -> list[Candidate]`，后续可以替换为 OCR/模型组合，复用快照、分页、布局和点击逻辑。

这些是视觉候选，不是确定可交互控件。说明文字、光标、同一列表项的图标与文字仍可能分别出现；相邻分辨率数字也可能分成多个候选。没有文字 OCR 或控件语义分类，因此 agent 需要根据画面判断目标。检测遗漏时保留原始 `mouse` 工具和逐级网格定位。

## 点击有效性

每个 MCP 进程最多保留 4 个快照，默认有效期 120 秒。分页/细分不重新拍摄，点击则重新获取一帧并检查：

1. 快照仍存在、未过期。
2. 人工接管标记未阻止 MCP；同一运行目录的输入 revision 未变化。
3. 新旧图像分辨率一致。
4. 全局缩略图和目标附近的像素变化未超过阈值。

所有 MCP 输入完成或失败后，应用推进 `input-revision`；人工会话进入、退出也推进。成功点击后清空该进程的快照。这样可以拦截滚动、其他 MCP 操作、人工接管后继续使用旧编号的情况。

图像检查是启发式：模糊后的 RGB 差异超过 24/255 才视为变化；全局变化比例超过 4% 或目标邻域超过 8% 时拒绝。小幅无关时钟变化通常被容忍。它不保证检测所有外部变化，也无法消除截图与物理输入之间的时间间隔。ROCK 截图复用的共享帧新鲜度仍受现有两秒缓存规则限制；不能将这里的检查描述成原子画面点击。

## 不连接硬件调试

导出 PNG、元数据和静态 HTML 检查页：

```sh
uv run kvm-inspect /path/to/screenshot.jpg --output artifacts/vision/example
python3 -m http.server 8765 --bind 127.0.0.1 --directory artifacts/vision
```

打开 `http://127.0.0.1:8765/example/`，选择编号即可在原图中查看候选框和点击点。页面不会发送输入；导出文件中的快照 ID 不能用于另一个 MCP 会话。

也可以把保存的真实截图提供给 MCP 客户端，直接调试多图返回和区域细分：

```sh
KVM_SCREENSHOT=/path/to/screenshot.jpg uv run kvm-mcp \
  --backend image \
  --runtime /tmp/kvm-vision-replay
```

该回放源只读，所有实际输入请求都会报错。输入转发的自动测试使用记录后端，验证原图坐标、归一化坐标、错误时不发送输入和会话关闭。

从板子读取新鲜的真实 HDMI 帧，用于新的观察轮次：

```sh
uv run python scripts/capture_frame.py r5p --output artifacts/vision/live.jpg
uv run kvm-inspect artifacts/vision/live.jpg --output artifacts/vision/live
```

读取脚本只复制最近两秒内的共享帧，不启动采集、不发送 HID、不改变人工接管状态。

## 2026-10-20 本地检查记录

本轮开始连接板子时，`kvm-web` active、USB configured，但 HDMI 查询返回 `No locks available`，共享帧不存在。因此先用已保存的真实 HDMI 图调试。后续复查时 HDMI 恢复，已通过 SSH 读取当场真实画面：1920×1080 的 Zen 浏览器 Autodesk 页面，读取时共享帧年龄约 26ms。人工接管标记仍在，未执行板端点击。

| 已保存的真实画面 | 当前候选数 | 区域数 | 默认返回页数 |
| --- | ---: | ---: | ---: |
| Zed 编辑器 | 58 | 7 | 3 |
| macOS 键盘设置 | 118 | 13 | 5 |
| macOS 显示设置 | 140 | 13 | 5 |

当场采集的网页有大幅建筑插画，检测出 266 个候选、23 个细分区域（全部输出需要 8 页）。逐张观察后加入高密度总览：现在首个 MCP 返回只有一张九区域总览；选择右下角 C09 后，返回总览与两张局部图，共 24 个候选，每张分别 14/10 个。弹窗关闭按钮在局部图中为 T234，原图点击中心 `(1808.5, 818.5)`；它与旁边插画的线条候选可以清楚区分。

逐张查看标记图后进行了三项调整：

- 编辑器菜单的 Go/Run 在单纯边缘检测时漏检，局部对比度补充后出现对应标记。
- 孤立鼠标光标导致工具栏分图过高，按大间隙拆分后成为单独区域。
- 设置页的重复图标框和文字碎片较多；合并后键盘设置页从 144 个候选降至 118 个，显示设置页从 152 个降至 140 个。候选减少不等于点击成功率提升。

通过浏览器打开实际导出的检查页，点击 T055 后页面显示原图点击点 `(1570, 398.5)`，对应键盘设置中的“编辑…”区域。浏览器截图接口在本轮返回 `Unable to capture screenshot`，因此报告的可视证据采用导出的标记 PNG；网页事件通过浏览器实际交互验证。

自动测试还通过真实 stdio MCP 验证了多张 ImageContent、分页、网格和按 ID 点击调用；这些调用的输入目标是测试记录后端。真实 HDMI → 新工具 → USB → 目标界面反馈的闭环仍需在交还人工控制后验收。

本地证据位于被 Git 忽略的 `artifacts/vision/zed-v2/`、`settings-v2/`、`display-v2/`。当场原图与采集元数据是 `live-now.jpg` / `live-now.capture.json`，最新完整检查页在 `live-refined/`，C09 工具返回图在 `live-focus/`。PNG、原图、`manifest.json` 和检查页均保留，方便继续比较。
