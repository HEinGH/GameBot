# 历史会话进展

## 1. 项目概述

从零搭建一个基于图像识别的自动化清体力脚本（GameBot），包含完整的状态机工作流、可视化 GUI 配置界面、角色库独立管理、连招文件独立管理、隐身反检测、开发者调试工具、模板自学习、以及各类辅助功能（连招录制、后台窗口管理等）。**必须以管理员权限运行**以绕过 Windows UIPI 限制。最终交付可直接运行的项目代码和打包脚本（exe 内嵌管理员 Manifest）。

## 2. 技术难点和解决方案

| 难点 | 解决方案 |
|------|---------|
| **全屏游戏下 pynput 钩子失效** | 热键改用 `RegisterHotKey` Win32 API；录制改用 `GetAsyncKeyState` 轮询（10ms 间隔） |
| **tkinter 布局溢出** | 改用每行最多 3 个配置项的固定布局 |
| **编辑角色对话框连招列表不可见** | 先 pack `bottom` 再 pack `combo_frame` |
| **PyInstaller 打包后日志路径** | 使用 `sys.executable` 获取 exe 路径作为日志根目录 |
| **预设 UI 编辑被 Bot 启动时覆盖** | 去掉磁盘重读，直接使用 `self.preset_data` |
| **截图工具颜色失真** | 直接对 BGR 数据调用 `imencode`，OpenCV 内部完成 BGR→RGB 转换 |
| **中文文件名乱码** | 改用 `cv2.imencode` + `open()` 手动写入；`cv2.imread` → `np.fromfile` + `cv2.imdecode` |
| **游戏拦截所有软件鼠标点击** | `SetCursorPos` 定位 + `mouse_event` 长按（150-250ms）可绕过；关闭 `pydirectinput.FAILSAFE` |
| **模板匹配缩放范围太窄** | `scale_range` 从 `(0.8,1.2)` 扩大到 `(0.7,1.35)`，7 步采样，>1920px 降采样；覆盖窗口化(0.67x)~全屏(1.33x)全尺寸 |
| **角色列表超一屏（12角色/6可见）** | 窗口相对坐标 + 滚轮翻页（-600=5格/次），4 次匹配/1.2s 触发滚动 |
| **同名启动器干扰窗口查找** | `WindowManager` 选最大可见非最小化窗口，回退搜索过滤 >1000×700px |
| **隐身贝塞尔移动被游戏解释为视角转动** | 副本内按钮点击传 `bezier=False`，跳过贝塞尔直接 `SetCursorPos` 瞬移定位 |
| **角色模板跨预设重复维护** | 角色库解耦：`config/characters/` 独立存储模板，预设 JSON 只存引用 + 覆盖 |
| **NPC 寻路图标被窗口边缘 UI 误匹配** | 位置连续性校验（跳变 >30% 帧宽拒绝）+ soft-fallback（跟踪中降阈值 0.65 接续）+ scale_range 收紧 |
| **颜色校验对小图标（半透明边缘）不可靠** | 小图标去掉颜色校验，靠连续性校验 + 高阈值防御；大实体 UI 按钮保留颜色校验 |
| **开发者模式窗口失焦导致键盘落空** | fallback 窗口检测注入 `wm._window`，开机时点击窗口中央触发 Windows 自动聚焦 |
| **兜底连招 ~1.7s 延迟衔接** | `idle_cycles` 从 50 帧降至 1 帧，自定义连招放完立即加载兜底 |
| **pydirectinput.moveRel 窗口边缘失效** | `rotate_camera`/`rotate_camera_free` 改用 Win32 `mouse_event(MOUSEEVENTF_MOVE)` 分步发送相对位移，替代 `pydirectinput.moveRel` |
| **domain_loading 期间鼠标随机晃动导致镜头转动** | 从 `_SAFE_STEALTH_STATES` 白名单移除 `domain_loading`，该状态下不再触发 `occasional_look_around` |
| **小模板（技能栏/结算画面）置信度每天波动** | 边缘腐蚀（面积<3000px 灰度图 erode 1px 去抗锯齿过渡区）+ `reject_flip=True`（拒绝翻转误匹配）+ `auto_update`（匹配成功后自动更新模板文件） |
| **连招内联存储复用困难** | 连招与角色解耦：`combos/` 独立存储 `.json` 文件，角色/预设只存文件名引用；`resolve_characters()` 自动迁移旧内联格式 |
| **角色列表 Treeview 与预设页风格不统一** | 替换为 Canvas + 行内 Spinbox/Combobox 实时控件列表，与 ChainStepList 风格一致 |
| **Windows UIPI 阻止低权限进程向游戏发送输入** | GUI 以管理员身份运行；PyInstaller 打包时加 `--uac-admin` 嵌入管理员 Manifest；`start.bat` 自动检测并提权 |

## 3. 关键文件架构

```
game_bot/
├── main.py / gui.py / gui.pyw    # 入口
├── build.py / start.bat / requirements.txt
├── migrate_characters.py         # 一次性迁移脚本
├── config/
│   ├── settings.py / settings.json
│   ├── presets/                  # 预设 JSON（角色引用格式）
│   └── characters/               # 角色库（独立角色模板 JSON）
├── core/
│   ├── fsm.py                    # 状态机 (BaseState + FSM + 中文映射)
│   ├── blackboard.py             # 线程安全上下文
│   └── watchdog.py               # 卡死检测（SSIM）
├── capture/
│   └── screen.py                 # 屏幕捕获（dxcam/mss）
├── recognition/
│   ├── template.py               # matchTemplate 多尺度 + 颜色/翻转校验
│   ├── npc_detector.py           # ORB + FLANN NPC 检测
│   └── portal_detector.py        # ORB + 模板匹配出口检测
├── input/
│   └── controller.py             # Win32 输入模拟（含隐身 bezier 移动）
├── combos/
│   ├── executor.py               # 连招执行器
│   └── *.json                    # 录制的连招文件
├── states/                       # 10 个活跃状态
│   ├── character_select.py / town_nav.py / npc_navigate.py
│   ├── domain_loading.py / domain_combat.py / dungeon_exit_nav.py
│   ├── map_loading.py / town_exit.py / complete.py / stuck_recovery.py
│   └── result_screen.py / exit_nav.py / exit_menu.py (保留未注册)
├── gui/
│   └── app.py                    # tkinter GUI（含角色库/预设管理/开发工具）
├── utils/
│   ├── antidetection.py / logger.py / window_manager.py
│   ├── macro_recorder.py / notify.py / geometry.py
└── templates/                    # 截图模板
```

### 关键设计决策

1. **状态机**：每个状态继承 `BaseState`，实现 `enter()`/`update()`/`exit()`，通过 `FSM.transition()` 切换
2. **配置分层**：预设级全局配置 + 按角色配置，后者优先
3. **线程模型**：tkinter 主线程 + Bot 后台线程，通过 `Blackboard`（加锁）共享状态
4. **日志**：三路文件（常规/错误/崩溃），`RotatingFileHandler` 限制大小
5. **输入模拟**：Win32 `SetCursorPos` + `mouse_event` 长按点击，不依赖 Interception 驱动
6. **窗口定位**：`WindowManager` 取最大可见非最小化窗口，导航基于 `_window_rect` 窗口相对坐标

### 启动命令

**必须以管理员权限运行**（Windows UIPI 要求），可直接双击 `start.bat`（自动提权）或打包后运行 exe（内嵌 Manifest）。

```bash
python gui.py                          # GUI 模式（推荐，需管理员终端）
python main.py --list                  # 列出预设
python main.py -p default -c 2         # CLI 模式（需管理员终端）
python main.py --record-combo my_combo # 录制连招
python build.py                        # 打包（exe 内嵌 --uac-admin）
```

## 4. 当前状态

### 状态机流程

```
CHARACTER_SELECT → TOWN_NAV → NPC_NAVIGATE → DOMAIN_LOADING → DOMAIN_COMBAT
     ↑                                                              ↓
     │                                                     DUNGEON_EXIT_NAV
     │                                                     ├── 再次挑战 → DOMAIN_LOADING ↻
     │                                                     └── 退出副本 → MAP_LOADING
 TOWN_EXIT ←──────────────────────────────────────────────────────┘
     ├── 切换角色 → CHARACTER_SELECT ↻
     └── 全部完成 → COMPLETE (停止)
```

### 模块验证状态

| 模块 | 状态 |
|------|------|
| character_select（选人+进游戏） | ✅ |
| town_nav（城镇→副本，统一链式操作） | ✅ |
| npc_navigate（NPC 寻路） | ✅ |
| domain_loading → domain_combat（战斗→结算→兜底连招） | ✅ |
| dungeon_exit_nav（出口寻路→再次挑战/退出） | ✅ |
| map_loading → town_exit → character_select（循环） | ✅ |
| 多角色循环 | ✅ |
| 切换角色测试 | ✅ |
| 预设管理 GUI | ✅ |
| 角色库独立管理 | ✅ |
| 角色库修改即时生效（自动刷新预设） | ✅ |
| 连招与角色解耦（combos/ 独立文件 + 自动迁移） | ✅ |
| 连招元数据补全（source + duration_sec） | ✅ |
| 连招库模糊搜索 | ✅ |
| 角色列表控件化（Canvas + 内联 Spinbox/Combobox） | ✅ |
| 模板置信度可配置化（含颜色/翻转校验） | ✅ |
| 颜色校验（变量bug+ROI resize已修复） | ✅ |
| 确认进入多步骤链 | ✅ |
| NPC 寻路连续性校验 + soft-fallback | ✅ |
| 副本出口寻路连续性校验（seek/center 阶段放宽） | ✅ |
| GUI 启动快捷键 | ✅ |
| 隐身模式三层方案（domain_loading 已从白名单移除） | ✅ |
| 开发者工具状态选择器 | ✅ |
| "完成后退出"选项 | ✅ |
| scale_range 全局收紧 (0.7,1.35) | ✅ |
| 兜底连招即时衔接 | ✅ |
| rotate_camera / rotate_camera_free Win32 重写 | ✅ |
| 小模板边缘腐蚀 + reject_flip + auto_update | ✅ |
| 开发者工具模板批量匹配测试 | ✅ |
| 连招录制+管理 | ⏳ 待测试 |
| 后台模式测试 | ⏳ 待测试 |

---

## 5. 会话 1（历史）

| 文件 | 关键改动 |
|------|---------|
| `main.py` | CLI 入口，添加 `--stealth`、`--background`、`--window-title`、`--secondary-monitor`、`--record-combo` 参数；修正 `total_chars` 默认值逻辑 |
| `gui.py` / `gui.pyw` | GUI 入口，`pythonw.exe` 无控制台启动 |

---

## 6. 会话 2（新增/修改）

### 状态修复

| 文件 | 关键改动 |
|------|---------|
| `states/exit_menu.py` | 修复模板读取：`rechallenge_template` / `exit_domain_template` 增加 `town_nav` 后备查找 |
| `states/town_nav.py` | Ctrl → Alt 键呼出鼠标；新增头像检测超时兜底（60次 → 强制继续） |
| `states/town_exit.py` | 修复 `_max_attempts` 负值；修复 step 2 中 `all_done` 与 `exit_game_template` 空的 fallthrough |

### GUI

| 改动 | 说明 |
|------|------|
| 截图工具 | 全屏截图 → 裁剪选择区域 → 保存到 `templates/`；修复颜色失真（BGR→RGB 编码修复）+ Unicode 文件名支持 |
| 连招录制重做 | 去掉预输入名称要求；F5/F6 全局热键改用 `RegisterHotKey`（全屏兼容）；录制完成后弹出保存命名对话框 |
| 连招管理 | 新增「已保存的连招文件」表格；支持预览、删除、打开文件夹；绑定到角色功能 |
| 文件选择器 | 所选文件自动复制到 `templates/`，带重命名冲突处理 |
| Ctrl→Alt | 配置项标签、变量名、JSON 字段全部改为 Alt |
| result_screen_template | 从全局配置移除，改为按角色配置 |

### 其他

| 文件 | 改动 |
|------|------|
| `utils/macro_recorder.py` | 完全重写：去掉 `pynput` 钩子，改用 `GetAsyncKeyState` 轮询（全屏兼容），新增 `_VK_MAP` |
| `input/controller.py` | `ctrl_press()`/`ctrl_release()` → `alt_press()`/`alt_release()` |
| `config/presets/default.json` | 字段移动到正确的层级；`ctrl_for_mouse` → `alt_for_mouse` |
| `build.py` | 补齐 hidden-import（plyer、queue、tkinter 等） |
| `requirements.txt` | 更新为完整依赖列表 |

---

## 7. 会话 3（Debug & 稳定化）

| 文件 | 关键改动 |
|------|---------|
| `states/character_select.py` | **完全重写**：三步流程（点头像 → 等 2s → 点进入），去掉跳过选角色功能；滚动逻辑集成（窗口相对坐标 + 鼠标滚轮翻页）；`jitter_delay` 随机化等待；匹配间隔优化到 0.3s |
| `states/town_nav.py` | 修复无头像模板 / 空副本序列时永久卡死；添加匹配冷却（0.8s）防止过频搜索 |
| `states/town_exit.py` | 修复 `settings_template` 为空时永久卡死；`"return"` → `"enter"` 键名修正 |
| `states/exit_nav.py` | 镜头旋转从 `move_rel` 改为 `controller.rotate_camera()`（按住右键拖拽） |
| `states/stuck_recovery.py` | `"return"` → `"enter"` 键名修正；`_KEY_MAP` 补齐 `"m"` |
| `states/domain_combat.py` | 移除未使用的 `Controller` import |
| `states/domain_loading.py` | 移除未使用的 `Settings` import |
| `input/controller.py` | **核心重构**：`click_at` 改用 Win32 `SetCursorPos` + `mouse_event` 长按，关闭 `pydirectinput.FAILSAFE`；新增 `mouse_scroll()` 滚轮方法；新增 `rotate_camera()`；`_KEY_MAP` 补 `"m"`；`jitter_delay` 供状态机随机化延迟 |
| `gui/app.py` | 异常崩溃 cleanup 代码移到 `finally` 块；`AVAILABLE_KEYS` 补 `"left_alt"`；游戏窗口自动查找（选最大可见非最小化窗口 + 激活焦点）；`_window_rect` 存入 blackboard |
| `recognition/template.py` | `cv2.imread` → `np.fromfile` + `cv2.imdecode`（修复中文路径）；`scale_range` 扩大到 `(0.5,1.5)`、`scale_steps` 增至 11；>1920px 帧自动降采样 |
| `core/watchdog.py` | `cv2.imwrite` → `cv2.imencode` + `open()`（Unicode 安全） |
| `main.py` | `--secondary-monitor` 不再依赖 `--background`；清理 dead code |
| `utils/window_manager.py` | `find_window` 改为选取**最大可见非最小化窗口**（过滤隐藏启动器）；新增 `rect` 属性 |

---

## 8. 会话 4（NPC 寻路 & 副本完整流程）

### 8.1 新增状态：npc_navigate

基于 NPC 头顶图标引导角色自动寻路至 NPC 旁边。

| 阶段 | 触发条件 | 行为 |
|------|---------|------|
| `scan` | 进入状态 | 全屏搜索 NPC 图标（threshold=0.35, scale_range 0.3-1.5, 15 steps） |
| `seek` | 图标在窗口下半区 | 逐帧旋转镜头（25°/次），每 6 帧丢失自动反转方向 |
| `center` | 图标在上半区 | 比例旋转（10/20/35° 三档）水平居中；±1.5% 屏宽视为已居中 |
| `move` | 居中完成 | 按住 W 前进，每 2 帧检查偏移，>6% 回 center 重校 |
| `recover` | 前进中卡地形 | 跳→左横移→右横移→ESC 回退 |
| `enter` | 到达 NPC 旁 | 寻找确认弹窗模板（conf≥0.75 非 enter 阶段 / ≥0.50 enter 阶段），点击 → domain_loading |

关键设计：坐标系基于 `_window_rect` 的窗口中心（而非帧中心）；镜头旋转用 `pydirectinput` 左键拖拽。

### 8.2 town_nav 重构

| 改动 | 说明 |
|------|------|
| 合并统一链 | 头像检测 → 链式操作（daily_button + domain_select_steps + challenge_templates 三合一） |
| `_try_transition()` | 链完成后检测 NPC 图标 → 跳转 `npc_navigate`；未配置 NPC 模板则直走 `domain_loading` |
| 匹配阈值 | 链式操作用 0.45；NPC 图标用 0.40；首个链动作点击后等待 2.0s |

### 8.3 连招系统修复

| 文件 | 改动 |
|------|------|
| `combos/executor.py` | `repeat` 拆分：`load_combos()` 将 `repeat=N` 展开为 N 个独立动作 |
| `combos/executor.py` | 延迟高斯抖动：每次前后延取值独立（高斯分布 + 4% 微停顿） |
| `combos/executor.py` | 右键屏蔽：所有按键方法跳过 `right_click`/`right` |
| `states/domain_combat.py` | 结算检测移到连招队列为空后且 >3s；兜底连招支持预设级 `fallback_combos` |

### 8.4 输入与 GUI 增强

- 输入统一：`click_at` → `pydirectinput.moveTo + mouseDown/Up`；键盘用 `pydirectinput.keyDown/Up` + `_PDI_KEY_MAP`
- GUI：新增 `ChainStepList` 组件（链式步骤列表）、兜底连招文本框、PanedWindow 高度可调
- 启动记忆：`settings.json` 保存 `last_preset/char_count/stealth/background`

---

## 9. 会话 5（副本流程重构 & Debug）

### 9.1 副本战斗结算面板

`domain_combat.py` 完全重写：技能连招 → 保底连招循环 → 识别角色结算面板（阈值 0.30）→ `click_at` 点击关闭 → 非阻塞 `_dismissing` 子状态。`click_at` 改为 Win32 `SetCursorPos` + `mouse_event`。

### 9.2 新增状态：dungeon_exit_nav

替代旧三状态（result_screen + exit_nav + exit_menu），统一管理副本内寻路到出口和按钮交互。

| 阶段 | 行为 |
|------|------|
| `scan` | PortalDetector 搜索出口图标 |
| `seek` | 旋转视角把图标转到上半区 |
| `center` | 比例旋转（10/20/35°）水平居中 |
| `move` | 按住 W 前进，每 2 帧复查偏移 |
| `buttons` | 出口接近时点再次挑战/退出→确认 |

关键设计：坐标系与 NPC 寻路完全一致；转动视角用 `rotate_camera_free`（不按左键，避免触发攻击）；帧率限制 `_interval=0.12`。

### 9.3 PortalDetector 增强

ORB 匹配数 10→6，Lowe 比率 0.75→0.80；新增模板匹配回退（`find_template`，threshold=0.40, scale 0.3-1.5, 13步）。

### 9.4 Bug 修复汇总

| Bug | 修复 |
|-----|------|
| `exit_nav` 用 `GetSystemMetrics` 算偏移（多屏错位） | 改用帧尺寸 `frame.shape[:2]` |
| `domain_combat` return 后死代码 | 删除 |
| `click_at` 用 `pydirectinput.moveTo`（全屏不响应） | 改为 `SetCursorPos` + `mouse_event` |
| `_wait_panel_gone` 阻塞主循环 | 改为非阻塞 `_dismissing` 子状态 |
| 寻路无帧率限制 | 加 `_last_ts` / `_interval=0.12` |
| 寻路无崩溃兜底 | 包 try/except → domain_loading |
| `exit()` 未释放鼠标按键 | 补 `mouseUp` |
| 旋转步长过大 | 改为 10/20/35° |
| 空角色列表静默死循环 | 改为 `blackboard["running"] = False` |

### 9.5 保留但未注册的旧状态

`result_screen.py`、`exit_nav.py`、`exit_menu.py` 已从 FSM 移除，保留在磁盘供参考。

---

## 10. 会话 6（主流程贯通 & 日志/UI 优化 & Bug 修复）

### 10.1 状态机修复

| 改动 | 说明 |
|------|------|
| `town_exit → complete` | 原直接设 `running=False`，改为 `fsm.transition("complete")` |
| `domain_combat` dismiss | 结算面板关闭简化：等 1s 重试，最多 2 次后强退 |
| `dungeon_exit_nav` 按钮检测 | `_do_update` 顶部增加按钮预检（与 NPC 寻路对齐），`_near_exit` 控制阈值降级 |
| `dungeon_exit_nav` portal close | scan 去掉 `or btn`，size 20000→50000；按钮阈值分场景（scan 0.65 / click 0.55） |
| `_find_window_rect` 窗搜自愈 | 三状态均加自愈窗口搜索（优先按 `window_title` 匹配） |
| `town_exit` 窗口激活 | `enter()` 点游戏窗口中央确保焦点（解决 GUI 启动后 ESC 无效） |
| 寻路坐标重构 | `npc_navigate` 和 `dungeon_exit_nav` 统一改显式窗口相对坐标，去屏幕中心 fallback |
| PortalDetector 模板匹配 | `_match_template` 灰度图重复转换异常，改为传 BGR 原始帧 |

### 10.2 `domain_run_count` off-by-one

`runs_done >= domain_runs` → `runs_done + 1 >= domain_runs`。runs=3 时第 3 次战斗结束就退出。

### 10.3 日志系统优化

| 改动 | 说明 |
|------|------|
| 日志合并 | 移除"运行日志"标签页，输出到运行控制面板 |
| 中文 INFO | 状态切换：`状态切换: 副本战斗 → 副本出口寻路`；识图：`识图成功: xxx.png 位置(x,y) 置信度=0.xxx` |
| DEBUG 分级 | 扫描/寻路/细节日志改 DEBUG；面板加 INFO/DEBUG 勾选框 |

### 10.4 GUI 重构

| 改动 | 说明 |
|------|------|
| 导航栏 | 去掉"运行日志"；新增"开发者工具"；"连招录制"→"连招录制（待测试）"；"后台"→"后台（待测试）" |
| 按钮 | "编辑"→"确定"；窗口标题 Entry 加宽 |
| 删除箭头图标 | `PortalDetector`、`dungeon_exit_nav`、GUI 全部移除 |
| 出口图标 | 从"出口寻路"移入"副本战斗"区 |
| 预设管理布局 | 重排：进入游戏 → 城镇导航 → 副本战斗 → 退出流程 → 角色列表 |
| 列表高度 | 去 PanedWindow 拖拽，固定高度（操作链 140px / 5 行，角色表 6 行） |
| 兜底连招 | Text 文本框 → `FallbackComboDialog`（复用 `ActionDialog`），按钮在角色表栏 |
| 删除预设 | 运行控制栏新增"删除"按钮 |
| 配置统一 | `char_count`/`stealth`/`background` 存入预设 JSON；`settings.json` 只记 `last_preset` |
| 启动加载 | `_load_last_preset` 在 UI 构建前执行 |

### 10.5 Bug 修复（Review）

| Bug | 修复 |
|-----|------|
| GUI 窗口 fallback 检测 `continue` 后死代码 | `continue` 移到 try/except 外 |
| `window_mgr` 仅 bg 赋值导致焦点循环死代码 | 窗口找到就赋值 |
| `_find_window_rect` 无用 `candidates = []`（3 处） | 删除 |
| `_update_fallback_label` 空 `pass` | 移除 |
| `_save_preset_as` 调已删除的 `_save_dash_settings` 静默崩溃 | 删除残留调用 |
| `_dismiss_panel_pos` 每帧被 `update()` 无条件重置 | 删除错误缩进赋值 |

### 10.6 新增功能

| 功能 | 说明 |
|------|------|
| 跳过刷本测试切换角色 | 勾选后从 `town_exit` 开始，放在开发者工具页 |
| 执行角色数 | 范围按预设角色数动态截断；启动时从预设 JSON 读取 |

### 10.7 新增文件

| 文件 | 说明 |
|------|------|
| `README.md` | 完全重写，反映当前实际代码状态 |

---

## 11. 会话 7（Debug 收尾 & 交付化 & 隐身方案）

### 11.1 基础架构修复

| 文件 | 改动 |
|------|------|
| `gui/app.py` | 主循环中 `occasional_look_around()` 调用（stealth=True 且 fsm.current != "domain_combat"） |
| `main.py` | CLI 主循环同步追加 `occasional_look_around()` |
| `gui/app.py` | F5/F6 录制热键添加 `_hotkeys_enabled` 标志位，Bot 运行时禁用热键，停止后恢复 |
| `capture/screen.py` | `stop()` 中 `del self._capture` 修复 dxcam 实例泄漏；`start()` 中 `ValueError` 专项 catch 静默 dxcam signal 报错；dxcam logger 级别设为 WARNING |
| `gui/app.py` | `_start_bot` 空角色列表弹窗校验；正常模式也激活游戏窗口（解决 `pydirectinput` 输入不到游戏的根因） |
| `utils/antidetection.py` | 删除未使用的 `random_keystroke_rhythm()` |

### 11.2 模板置信度可配置化

| 文件 | 改动 |
|------|------|
| `config/settings.py` | 新增 `parse_template_ref(value)` → `(name, threshold)`；`parse_template_chain(list)` → `[(name, threshold), ...]`；`DEFAULT_TEMPLATE_THRESHOLD = 0.65` |
| `states/character_select.py` | portrait/enter 模板改用 `parse_template_ref` 解析 |
| `states/town_nav.py` | avatar/npc/chain 步骤模板统一解析；`_build_chain` 返回 `(name, thr)` 元组列表 |
| `states/npc_navigate.py` | npc/enter 模板解析；确认进入改为链式遍历（见 11.7） |
| `states/dungeon_exit_nav.py` | rechallenge/exit/confirm/portal 模板解析；`_find_button` 使用解析阈值 |
| `states/town_exit.py` | settings/switch/exit_game/confirm 模板解析 |
| `states/map_loading.py` | avatar 模板解析 |
| `states/domain_loading.py` | skill_bar 模板解析 |
| `states/domain_combat.py` | result_screen 模板解析 |
| `recognition/portal_detector.py` | 构造器新增 `template_threshold` 参数 |
| JSON 格式 | 纯字符串存为 `"xxx.png"`；自定义阈值存为 `{"template":"xxx.png","threshold":0.55}` |

### 11.3 GUI 阈值配置

| 改动 | 说明 |
|------|------|
| 全局配置 | 所有模板字段右侧新增阈值 Spinbox（默认 0.65，步进 0.05，范围 0.30-0.99） |
| 链式组件 | `ChainStepList` 每步新增阈值 Spinbox，支持字符串/对象混合存储 |
| 角色对话框 | 4 个模板字段（选人头像/技能栏/结算/城镇头像）各新增阈值 Spinbox |
| 序列化 | `_pack_tpl_value()` / `_unpack_tpl_value()` 提升为模块级函数 |

### 11.4 日志面板优化

| 改动 | 说明 |
|------|------|
| 滚动条 | `dash_status_text` 外包 `log_container` + 垂直 Scrollbar |
| 最大行数 | `_log_max_lines = 500`，`_trim_log_lines()` 超出自动删旧行 |
| DEBUG 修复 | `_on_log_level_change` 同步 `logging.getLogger().setLevel()`，子模块 DEBUG 可到达 GUI |

### 11.5 退出流程修复

| 文件 | 改动 |
|------|------|
| `states/dungeon_exit_nav.py` | `_do_buttons` stage 1 新增 `_confirm_attempts=20` 独立重试计数器（原 `_click_wait` 倒计时后仅搜 1 次确认）；stage 1 并行检测城镇头像 fallback（无确认弹窗时头像出现即判角色回城） |

### 11.6 寻路转速统一

| 参数 | 旧值 | 新值 |
|------|------|------|
| 帧间隔 `_interval`（npc + dungeon） | 0.12s | 0.08s |
| `rotate_camera` 总耗时 | ~0.20s | ~0.07s |
| `rotate_camera_free` 总耗时 | ~0.06s | ~0.03s |
| 旋转后 `time.sleep` | 0.08-0.15s | 0.05-0.08s |

### 11.7 确认进入多步骤链

| 文件 | 改动 |
|------|------|
| `states/npc_navigate.py` | `_enter_tpl/_enter_thr` 改为 `_enter_chain` 列表 + `_enter_chain_idx` 游标；enter 阶段从单次搜索改为遍历链（找到→点击→idx++→下步；全部完成→domain_loading） |
| `gui/app.py` | "确认进入" 单行 Entry 替换为独立 `ChainStepList` 组件（标签"确认进入链"）；`set_items/get_items` 兼容字符串和数组 |

### 11.8 GUI 易用性

| 改动 | 说明 |
|------|------|
| 弹窗定位 | `_center_on_parent()` 模块级函数，6 个 Toplevel 弹窗均居中于父窗口 |
| 去残影 | `withdraw()` → 布局 → `update_idletasks()` → `deiconify()`，单次调用消除闪烁 |
| 提示文字 | 城镇导航标题下扩展为 4 行说明（操作链/多确认/单确认/Alt/NPC 用途） |
| 布局 | Alt 复选框 + NPC 图标同一行，两个链式列表依次排列 |

### 11.9 隐身模式融入方案（待实施）

#### 分层架构

| 层 | 功能 | 影响范围 | 风险 |
|----|------|---------|------|
| 第一层 | 鼠标路径拟人 | `click_at` stealth=True 时改为 `move_to_bezier` 平滑移动 + `mouse_event` 点击，替代 `SetCursorPos` 瞬移 | 零风险 |
| 第二层 | 计时抖动 | `jitter_delay`/`tap_key` 在 stealth=True 时已走 `HumanDelay` 路径，无需改动 | 零风险 |
| 第三层 | 随机鼠标晃动 | `occasional_look_around` 改为白名单模式 | 零风险 |

#### 安全状态白名单

仅以下无鼠标操作的状态允许随机晃动：
`domain_loading` / `map_loading` / `complete` / `stuck_recovery`

#### 各状态鼠键审计

| 状态 | 鼠标操作 | 随机晃动 | 原因 |
|------|---------|:---:|------|
| `character_select` | `click_at`（选人、点进入） | ❌ | 点击间隙光标跳变 |
| `town_nav` | `click_at`（链式步骤），Alt 按住 | ❌ | Alt 使光标可见，随机移动干扰识别 |
| `npc_navigate` | `rotate_camera`（左键拖拽）+ `click_at` | ❌ | moveRel 与 mouseDown 并发冲突 |
| `domain_loading` | 无 | ✅ | 纯等待 |
| `domain_combat` | `click_at`（点结算面板） | ❌ | 鼠标移动→视角转动；左键→平A/上挑；右键→闪避/冲刺 |
| `dungeon_exit_nav` | `rotate_camera_free` + `click_at` | ❌ | 旋转中插入 moveRel 破坏方向 |
| `map_loading` | 无 | ✅ | 纯等待 |
| `town_exit` | `click_at`（菜单操作） | ❌ | 菜单光标跳变异常 |
| `complete` | 无 | ✅ | 已停止 |
| `stuck_recovery` | `click` + 按键 | ✅ | 恢复卡死，允许晃动尝试解困 |

#### 修改清单

| 文件 | 改动 |
|------|------|
| `input/controller.py:click_at()` | stealth=True → bezier 移动 + 点击 |
| `gui/app.py` 主循环 | `fsm.current != "domain_combat"` → `fsm.current in SAFE_STATES` |
| `main.py` 主循环 | 同上 |
| `input/controller.py` | 新增 `_SAFE_STEALTH_STATES` 常量 |

### 11.10 待完成项

| 项目 | 状态 |
|------|------|
| 隐身模式三层方案实施 | ⏳ 方案已评审，待实施 |
| 连招录制端到端测试 | ⏳ 待测试 |
| 深渊3 模板置信度 0.52 偏低 | ⏳ 需重截图或单独设阈值 |
| 后台模式测试 | ⏳ 待测试 |

---

## 12. 会话 8（主流程贯通 & 模板置信度可配置化 & 寻路优化 & 易用性）

### 12.1 基础架构修复

| 文件 | 改动 |
|------|------|
| `gui/app.py` | Bot 运行时 F5/F6 热键禁用，停止后恢复 `_hotkeys_enabled` |
| `gui/app.py` | `_start_bot` 正常模式也激活游戏窗口（解决 Alt 输入不到游戏的根因——窗口未聚焦） |
| `gui/app.py` | 空角色列表弹窗警告 |
| `capture/screen.py` | `stop()` 中 `del self._capture` 修复 dxcam 实例泄漏；`start()` 中 `ValueError` 专项 catch + dxcam logger 设 WARNING 静默 signal 报错 |
| `config/settings.py` | 新增 `get_writable_dir()`，打包后 `settings.json` 写入 exe 目录而非临时解压目录 |

### 12.2 模板置信度可配置化（核心架构）

| 文件 | 改动 |
|------|------|
| `config/settings.py` | 新增 `parse_template_ref(value)` → `(name, threshold)`，支持字符串或 `{"template":"x","threshold":0.55}` dict；`parse_template_chain(list)` → `[(name, thr), ...]`；`DEFAULT_TEMPLATE_THRESHOLD = 0.65` |
| 11 个 state 文件 | 所有 `find_template` 调用点改用 `parse_template_ref` 解析，阈值不再硬编码 |
| `recognition/template.py` | 新增 `_color_registry` + `_flip_registry` + `_hgram_cache`。`find_template` 增加颜色校验（BGR 三通道直方图相关性）和翻转校验（NCC 比较 `模板 vs flip(模板)`），写入 registry 的模板自动生效 |
| `recognition/portal_detector.py` | 构造器接受 `template_threshold` 参数 |

**JSON 格式**：
```json
"template_name": "xxx.png",                          // 字符串（阈值=0.65）
"template_name": {"template":"xxx.png","threshold":0.55,"color_threshold":0.7,"reject_flip":true}
```

**关键设计**：`color_threshold` 和 `reject_flip` 通过 `_color_registry`/`_flip_registry` 注入 `find_template`，**零调用者改动**。`parse_template_ref` 保留 2 返回值。

### 12.3 确认进入多步骤链

| 文件 | 改动 |
|------|------|
| `states/npc_navigate.py` | `_enter_tpl/_enter_thr` → `_enter_chain[]` + `_enter_chain_idx` 游标；确认阶段从单击改为遍历链（找到→点击→idx++→下步→全部完成→domain_loading）；新增 `_enter_attempts > 120` 超时→强制 transition |
| `gui/app.py` | "确认进入"从单行 Entry 替换为独立 `ChainStepList` 组件；`set_items/get_items` 兼容字符串/数组 |

### 12.4 寻路系统统一优化

| 参数 | 旧值 | 新值 | 说明 |
|------|------|------|------|
| NPC seek 角度 | 固定 25° | 自适应 15/30/50/65°（按 `h_ratio` 水平偏移分档）+ 纵向系数 ×0.7/1.0/1.3 | 背对时大步快转 |
| NPC seek sleep | 0.15s | 0.04s | 加快旋转频率 |
| 副本 seek 角度 | 固定 25° | 同 NPC 自适应表 | 接近中心时小步防过冲 |
| `_do_rotate` 微调 | 10/20/35° | 5/10/18° | 与 seek 步长表无缝衔接 |
| 切 center 门槛 | `h_ratio < 2%` | `h_ratio < 5%` | 提前切换，消除边界振荡 |
| `rotate_camera` sensitivity | 200 | 200（不变） | NPC 用 click-drag，200 为游戏拖拽上限 |
| `rotate_camera_free` sensitivity | 800 | 400 | 副本用 free move，800→400 减半 |
| `npc_navigate._interval` | 0.12s | 0.08s | |
| `dungeon_exit_nav._interval` | 0.12s | 0.08s | |

**关键**：NPC 寻路 `rotate_camera` 的 sensitivity 最终回归 200（sensitivity=400 时 222px 单次拖拽超出游戏上限被截断）；副本 `rotate_camera_free` 回归 400（800 偏大，400 合适）。

### 12.5 副本战斗

| 文件 | 改动 |
|------|------|
| `states/domain_combat.py` | `_max_reloads` 2→0：自定义连招播放一轮后立即切兜底连招循环（不再重放 3 轮自定义） |
| `states/domain_combat.py` | 移除 `self.executor.empty` 条件：每帧检测结算面板，命中后 `executor.clear()` 中断连招 + release_all() + 点击 dismiss |

### 12.6 退出副本流程

| 文件 | 改动 |
|------|------|
| `states/dungeon_exit_nav.py` | `_do_buttons` stage 1 新增 `_confirm_attempts=20` 重试计数器（替代原一次即弃逻辑） |
| `states/dungeon_exit_nav.py` | stage 1 并行检测城镇头像 fallback——无确认弹窗时头像出现即判角色回城 |
| `states/dungeon_exit_nav.py` | `_find_button` 修复 `exit_thr` 丢弃 bug（`exit_name, _` → `exit_name, exit_thr`） |

### 12.7 GUI 功能

| 改动 | 说明 |
|------|------|
| 日志面板 | 滚动条 + 最大 500 行 + `_trim_log_lines()`；DEBUG 复选框修复（同步 `logging.getLogger().setLevel()`） |
| 弹窗居中 | `_center_on_parent()`，6 个 Toplevel 均居中无残影 |
| 子目录支持 | `_import_template_file` 修复——`templates/` 子目录内文件返回相对路径（如 `子目录/图片.png`），不拷贝 |
| 链式列表保鲜 | `ChainStepList._sync_entries()` 通过 `old_items[i]` 保留 `color_threshold`、`reject_flip` 等额外字段 |
| 预设记忆 | `_on_preset_selected` 末尾加 `_refresh_char_table()`（修复启动后需手动点"确定"才能启动的 bug） |
| `char_start` / `char_count` / `log_debug` 记忆 | 统一存储在预设 JSON 中；`Settings().load()` 移到 `_load_last_preset` 之前 |
| "从第几个角色开始" | 新增 `dash_char_start` Spinbox，范围与角色数同步 |
| 启动快捷键 | `Ctrl+Alt+B` → `_poll_hotkeys` 20ms 轮询 `GetAsyncKeyState` 边沿触发（武装机制：先按住 Ctrl+Alt → 再点 B） |
| 连招按键 | `AVAILABLE_KEYS` 新增 `"p"` |
| 角色对话框 | "结算模板" → "结算画面" |
| 阈值 Spinbox | 全局配置 + 角色对话框 + 链式列表全部覆盖 |
| 提示文字 | 城镇导航标题下扩展说明（操作链/多确认/单确认/Alt/NPC 用途） |

### 12.8 技术难点

| 难点 | 解决方案 |
|------|---------|
| Alt 不响应的根因 | 非 `pydirectinput` 输入方法问题，而是 GUI 启动后游戏窗口未获焦点——`pydirectinput` 所有输入发到了桌面。修复：`_start_bot` 加窗口激活 |
| 临界3 vs NPC 同形误配 | `reject_flip` + `color_threshold` 双重校验，通过 `_registry` 注入 `find_template`，不改调用者 |
| `WH_KEYBOARD_LL` 钩子注册失败 | Windows 11 非管理员 Python 进程安全策略拒绝。回退到 `GetAsyncKeyState` 20ms 高频轮询 + 边沿触发武装机制 |
| 寻路 seek 振荡 | `h_ratio < 2%` 门槛太严→放宽到 5%；seek 角度表与 `_do_rotate` 统一后消除 3× 断崖跳变 |
| 多确认按钮副本 | `confirm_enter_template` 从单值扩展为链式组件 + `npc_navigate` enter 遍历链 |



---

## 13. 会话 9（隐身模式实施 & 角色解耦 & 寻路优化 & 冲刺修复）

### 13.1 隐身模式三层实施

| 层 | 功能 | 文件 | 改动 |
|----|------|------|------|
| 第一层 | 鼠标路径拟人 | `input/controller.py:105-122` | `click_at()` 新增 `bezier` 参数，stealth=True 时 `GetCursorPos` → `move_to_bezier`（贝塞尔曲线平滑移动 + `pydirectinput.moveTo` 逐段送鼠标事件），替代 `SetCursorPos` 瞬移 |
| 第二层 | 计时抖动 | 已有 | `tap_key`/`jitter_delay` 在 stealth=True 时走 `HumanDelay` 路径，无需改动 |
| 第三层 | 随机鼠标晃动白名单 | `gui/app.py:1860`, `main.py:176` | 全局常量 `_SAFE_STATES = {"domain_loading", "map_loading", "complete", "stuck_recovery"}`，仅这 4 个无鼠标操作的状态允许随机晃动；`domain_combat` 等有战斗输入的状态禁止 |
| — | `_SAFE_STEALTH_STATES` 常量 | `input/controller.py:15` | 新增模块级常量，供外部引用 |
| — | `_POINT` 结构体 | `input/controller.py:28-29` | `ctypes.Structure` 用于 `GetCursorPos` 获取当前光标位置 |

**非隐身模式下零影响**：`bezier` 和 `SAFE_STATES` 检查均被 `self.stealth` / `stealth` flag 短路，行为与改造前完全一致。

### 13.2 角色与预设解耦

| 阶段 | 文件 | 改动 |
|------|------|------|
| 数据层 | `config/settings.py` | 新增 `CHARACTERS_DIR`、`CHARACTER_PROFILE_FIELDS`（4 个模板字段）、`load_character_profile()`、`save_character_profile()`、`list_character_profiles()`、`resolve_characters()`、`serialize_characters()` |
| 迁移 | `migrate_characters.py` | 一次性脚本：从现有预设提取角色模板到 `config/characters/`，预设 JSON 的 `characters` 数组转为引用格式 |
| GUI 加载 | `gui/app.py` | `_load_last_preset()` / `_on_preset_selected()` / `_edit_selected_preset()` 加载后调 `resolve_characters()` |
| GUI 保存 | `gui/app.py` | `_save_preset()` / `_save_preset_as()` 保存前调 `serialize_characters()` 写回引用格式 |
| GUI 角色库 | `gui/app.py` | 新增"角色库"标签页：增删改排序角色模板；`_CharLibEditDialog` 编辑对话框；`_PickCharacterDialog` 添加角色选择器 |
| GUI 按钮 | `gui/app.py` | 预设页"添加角色"改为从角色库选择；"编辑角色"→"配置角色"；"删除角色"→"移除角色"；对话框标题同步更名；角色信息框新增提示文字 |
| CLI | `main.py` | 启动时调 `resolve_characters()` |

**设计决策**：内存中 `preset_data["characters"]` 始终存完整角色。仅在磁盘读写时做序列化/反序列化。模板字段（portrait/skill_bar/result_screen/avatar）存角色库文件，`runs`/`combos`/`fallback_combos` 存预设引用 dict。预设引用可覆盖模板（写回时做 diff，仅不同的字段写入）。

### 13.3 寻路系统多项优化

| 优化 | 文件 | 改动 |
|------|------|------|
| 模板缩放收窄 | `recognition/template.py:38-39` | 全局默认 `scale_range=(0.5,1.5)→(0.7,1.35)`, `scale_steps=11→7` |
| NPC 特化缩放 | `states/npc_navigate.py:113-115` | `_find_npc` 也收紧至 `(0.7,1.35)` |
| 出口检测缩放 | `recognition/portal_detector.py:62-63` | 模板匹配回退收窄至 `(0.7,1.35), 7步` |
| 位置连续性校验 | `states/npc_navigate.py:117-130` | `_find_npc` 内：距 `_last_pos` 水平跳变 > 30% 帧宽 → 拒绝。防止窗口右侧日常按钮误匹配 |
| Soft-fallback | `states/npc_navigate.py` / `states/dungeon_exit_nav.py` | 主阈值未匹配且已有 `_last_pos` 时，降阈值至 0.65 重试，通过连续性校验后接受。解决 NPC 转到暗色背景区域时置信度临时下降 |
| 贝塞尔跳过 | `states/dungeon_exit_nav.py:453,471` | 再次挑战/退出/确认按钮 `click_at(cx, cy, bezier=False)`，防止贝塞尔移动被游戏解释为视角转动 |
| Portal 连续性 | `states/dungeon_exit_nav.py:154-164` | `_find_portal` 同等连续性校验 |

**坐标系讨论**：曾尝试将 NPC/出口寻路改为纯帧坐标（去掉 `_gw_l/_gw_t` 的窗口锚点转换），发现游戏窗口偏左时帧中心 ≠ 窗口中心（差 ~100px），导致角色对着空气走。最终回退老方案（窗口锚点坐标系），老方案在 `_window_rect=None` 时 `enter()` 已正确将 `_gw_l/_gw_t=0`，配合 `_do_update` 帧尺寸回退，正常流程一直正确。

### 13.4 兜底连招衔接

| 改动 | 文件 | 说明 |
|------|------|------|
| `idle_cycles` 阈值 | `states/domain_combat.py:106` | `>= 50` → `>= 1`，自定义连招放完首个空帧即加载兜底连招，消除 ~1.7s 静默等待。前后延仍遵守 |

### 13.5 开发者工具升级

| 功能 | 文件 | 说明 |
|------|------|------|
| 状态选择器 | `gui/app.py:1197-1226` | `dash_skip_combat` 勾选框 → `dash_start_state` 下拉框（9 个状态可选），可从任意状态启动 Bot |
| 窗口聚焦 | `gui/app.py:1855-1863` | `_run_bot` 开机时若 `window_mgr` 存在但窗口未聚焦，点击窗口中央激活 Windows 自动聚焦。正常流程 `is_focused=True` 直接跳过 |
| Fallback 窗口 | `gui/app.py:1848` | fallback 路径检测到的 `pywinctl.Window` 注入 `wm._window`，确保 `window_mgr` 赋值，主循环焦点管理生效 |

### 13.6 GUI 易用性

| 改动 | 说明 |
|------|------|
| 仪表盘"保存"按钮 | 替换"刷新"，仪表盘可直接保存预设，避免忘记 |
| "完成后退出"勾选框 | 预设级配置，默认勾选。存 `exit_after_done` 字段，记忆功能完整。全部完成后跳 `complete` 停止，不退出游戏 |
| 阈值 Spinbox 锁定滚轮 | 所有 6 处 Spinbox 绑 `<MouseWheel>` → `"break"`，防止滚轮翻页误调阈值 |

### 13.7 Bug 修复

| Bug | 根因 | 修复 |
|-----|------|------|
| **颜色校验从未生效** | `template.py:119` 比较用 `color_threshold`（函数参数默认 0.0）而非 `ct`（registry 读取的实际阈值 0.7） | `color_threshold` → `ct` |
| **颜色校验 ROI 尺寸错位** | `_compute_hgram(template)` 用模板原尺寸，`_compute_hgram(ROI)` 用匹配到的 scale 尺寸，直方图分箱不同 | ROI resize 到 `(t_w, t_h)` |
| **兜底连招不接续** | `idle_cycles >= 50` 空等 ~1.7s，期间面板检测可能误触发 | `>= 1` |
| **隐身在副本战斗弹结算时旋转视角** | `click_at` 贝塞尔用 `pydirectinput.moveTo()` 被游戏解释为视角输入 | 再次挑战/确认按钮 `bezier=False` |
| **开发者模式失焦** | fallback 路径未设置 `window_mgr`，焦点检查被跳过 | fallback 找到的窗口赋给 `wm._window` |
| **`skip_combat` 变量残留** | 状态选择器改造后 `_start_bot` 仍有 `if skip_combat:` 引用 | 改为 `if start_state != "character_select":` |

### 13.8 待完成项

| 项目 | 状态 |
|------|------|
| 连招录制端到端测试 | ⏳ 待测试 |
| 后台模式测试 | ⏳ 待测试 |

---

## 14. 会话 10（代码清理 & 连招解耦 & UI 重构 & 实机调试）

### 14.1 代码清理

| 文件 | 改动 |
|------|------|
| `combos/executor.py` | 删除死代码：`execute_all()`、`stop()`、`_running`、`_current_action`、`_cycle_count`（被逐帧 `execute_next()` 替代） |
| `gui/app.py` | 删除 `_update_dash_status()`（被 `_poll_log` 替代） |
| `config/settings.py` | 删除 `DEFAULT_SETTINGS` 中已迁移到预设 JSON 的 4 个废弃字段（`last_char_count/last_char_start/last_stealth/last_background`） |
| `config/settings.json` | 同步删除 4 个废弃字段 |
| `main.py` + `gui/app.py` | `_SAFE_STATES` 去重复，统一 `import` 自 `controller._SAFE_STEALTH_STATES` |
| `config/settings.py` | `parse_template_chain` 补全 `color_threshold`/`reject_flip` 注册注入（此前仅 `parse_template_ref` 有此逻辑） |

### 14.2 连招与角色解耦

| 阶段 | 文件 | 改动 |
|------|------|------|
| 数据层 | `config/settings.py` | 新增 `COMBO_DIR`、`list_combos()`、`load_combo()`、`save_combo()`、`delete_combo()`、`_migrate_combo_to_file()`（自动命名 + 冲突处理 + 内容去重） |
| 解析 | `config/settings.py` | `resolve_characters(preset, preset_name)` 新增 `preset_name` 参数；检测旧格式（`combos` 是 list）自动迁移为 combo 文件引用；新格式（`combo` 是字符串）自动加载文件解析为运行时 actions |
| 序列化 | `config/settings.py` | `serialize_characters()` 写出 `combo`/`fallback_combo` 字符串引用，而非内联 actions 数组；`migrate_preset_fallback()` 处理预设级兜底迁移 |
| 运行时适配 | `gui/app.py:_run_bot` | 补全 `resolve_characters()` + `migrate_preset_fallback()` 调用（修复 Bot 启动时连招未解析的 bug） |
| 状态机 | `states/domain_combat.py` | `_load_fallback()` 优先读 `fallback_combo` 字符串→`load_combo()` 加载，向后兼容旧 `fallback_combos` 列表 |
| GUI 连招管理页 | `gui/app.py` | 侧栏"连招录制（待测试）"→"连招管理"；录制区 + 连招库 Treeview（名称/动作数/时长/来源）+ 详情 Treeview；新建/编辑/删除/预览/打开文件夹；`ComboEditDialog` 类 |

**删除的旧类**：`CharacterDialog`（185 行）、`FallbackComboDialog`（90 行）、`BindComboDialog`（115 行）。净减少约 450 行。

### 14.3 角色列表控件化

| 改动 | 说明 |
|------|------|
| Treeview → Canvas 控件列表 | 表头 + 滚动画布 + 行内 Spinbox(次数) + Combobox(连招) + 底部按钮栏，与 ChainStepList 风格统一 |
| 内联编辑 | 次数 Spinbox 直接修改（FocusOut/回车生效）、连招 Combobox 下拉选择（<<ComboboxSelected>> 即时生效） |
| 兜底连招 | 预设级按钮 `_pick_preset_fallback`，弹出下拉选择 combo 文件名 |
| 查看角色 | `CharacterViewDialog`：只读展示模板信息 + 连招详情 Treeview（动作列表） |
| 行选中 | 点击名称/行区域 → 浅蓝色高亮（`SelRow.TFrame` style），可连续上下移；点击列表外区域自动取消选中 |
| 按钮图标 | `+ 添加角色`、`△ 上移`、`▽ 下移`，与 ChainStepList 一致 |

### 14.4 操作链（ChainStepList）修复

| 改动 | 说明 |
|------|------|
| 行空白区域点击选中 | 补全 `<Configure>` 绑定使得 inner Frame 宽度跟随 Canvas 拉伸（此前定义了 `_on_canvas_configure` 但未绑定） |
| 步骤标签点击选中 | `step_label.bind("<Button-1>")` 补全 |
| 行内浏览按钮和 Spinbox 点击选中 | 补全绑定 → 点击即可选中行 |
| 移除 × 行内删除按钮 | 统一使用底部"删除"按钮 |
| 移除横向滚动条 | `scrollbar_h` + `_on_hmwheel` 删除 |
| 上下移后保持选中 | `_move()` 在 `_refresh()` 后调用 `_set_selected()` 恢复选中 |
| 选中高亮 | 统一为 `SelRow.TFrame` 浅蓝色 style |

### 14.5 页面滚动与画布

| 改动 | 说明 |
|------|------|
| 连招管理页滚动 | 整个页面包在 Canvas + Scrollbar 中（含标题/录制区/连招库） |
| 角色列表独立滚动 | `_char_canvas` 内嵌 Canvas，内容不足高度时滚轮无效 |
| 滚轮智能路由 | `_on_page_scroll`：事件来自 `_char_canvas` 或其子控件 → 只滚角色列表；否则滚页面画布 |
| `_should_forward_scroll` | Treeview/Combobox/Listbox/Text/Scrollbar/Spinbox 跳过；子窗口 `Toplevel` 跳过；`str` widget 守卫（被销毁控件不崩溃） |
| 内容不足时不滚动 | 所有 Canvas 在 `bbox("all")[3] <= canvas.winfo_height()` 时跳过 |

### 14.6 输入系统 Win32 重写

| 方法 | 旧实现 | 新实现 |
|------|--------|--------|
| `rotate_camera` | `pydirectinput.mouseDown/moveRel/mouseUp`（窗口边缘 moveRel 失效） | `mouse_event(LEFTDOWN)` → `mouse_event(MOVE, dx, 0)` 分 3 步 → `mouse_event(LEFTUP)` |
| `rotate_camera_free` | `pydirectinput.moveRel`（同上） | `mouse_event(MOVE, dx, 0)` 分 3 步，无按键 |
| `occasional_look_around` | 仍在用 `pydirectinput.moveRel`（适用非寻路场景） | 不变 |

**隐身模式白名单更新**：`_SAFE_STEALTH_STATES` 从 4 个减为 3 个（移除 `domain_loading`），实测发现加载画面中鼠标移动会转动镜头。

**副本按钮 bezier 恢复**：`dungeon_exit_nav.py` 中挑战/退出/确认按钮 `bezier=False` → `bezier=True`（之前归咎于 domain_loading 的随机晃动，根因已修复）。

### 14.7 模板匹配优化

| 优化 | 文件 | 说明 |
|------|------|------|
| 边缘腐蚀 | `recognition/template.py` | 面积 < 3000px 的灰度模板 `cv2.erode` 1px，削掉抗锯齿边缘过渡区 |
| 自动更新 | `recognition/template.py` | `auto_update=True` 时，匹配成功且面积 < 3000px 且置信度 > 0.75，用截图 ROI 覆盖模板文件 |
| `reject_flip` 全量启用 | `config/characters/*.json` | 7 个角色全部模板设为 `"reject_flip": true`（UI 元素不对称，翻转不可能匹配） |
| 颜色校验 | `config/characters/*.json` | portrait/avatar 开 `"color_threshold": 0.7`；skill_bar/result_screen 不开（面积小/边缘差异大，校验不可靠） |
| 副本出口连续性 | `states/dungeon_exit_nav.py` | `_find_portal` 连续性校验仅在 `move` 阶段生效，`seek`/`center`（旋转中）跳过跳变检查 |

**模板最佳尺寸区间**：

| 类型 | 最小 | 最大 | 理想 |
|------|------|------|------|
| portrait（选人界面头像） | 60×60 | 100×100 | 75×75 |
| skill_bar（技能栏） | 50×50 | 80×80 | 60×55 |
| result_screen（结算画面） | 60×60 | 150×150 | 80×80 |
| avatar（城镇头像） | 50×40 | 90×70 | 70×55 |

问题模板：`音爆/音爆技能栏.png`（39×38）、`狂战/狂战结算画面.png`（44×44）、`鬼刃/鬼刃技能栏.png`（49×50）需重截图。

### 14.8 开发者工具新增

| 功能 | 说明 |
|------|------|
| 模板批量匹配测试 | 开发者工具页新增按钮，截取当前屏幕 → 遍历所有角色库模板 → `find_template(threshold=0.10)` 宽泛搜索 → 输出置信度和位置到日志面板。独立线程运行，不影响状态机。 |

### 14.9 Bug 修复

| Bug | 根因 | 修复 |
|-----|------|------|
| **_start_bot 后 combo 未解析** | `_run_bot` 直接使用 `self.preset_data`，但 GUI 中 `_on_char_combo_changed` 改 combo 名后 pop 了 `combos` 列表 | `_run_bot` 中补 `resolve_characters()` + `migrate_preset_fallback()` |
| **rotate_camera 窗口边缘失效** | `pydirectinput.moveRel` 在窗口化模式边缘被 `ClipCursor` 钳制 | 改为 Win32 `mouse_event(MOVE)` 分步发送 |
| **domain_loading 期间镜头随机转动** | `occasional_look_around` 的 `pydirectinput.moveRel` 在加载画面中转动了镜头 | 从 `_SAFE_STEALTH_STATES` 移除 `domain_loading` |
| **副本出口寻路旋转中图标被 continuity check 误拒** | seek/center 阶段图标必然跳变，连续性校验不应适用 | **后续修正**：会话 10 已恢复全阶段连续性校验，改为配合边界校验 + 阻尼机制处理振荡 |
| **ChainStepList 行空白区域无法点击选中** | `_on_canvas_configure` 定义了但从未绑定 `<Configure>`，inner Frame 宽度不随 Canvas 扩展 | 绑定补全 |
| **绑 all MouseWheel 回调崩溃** | `event.widget` 可能返回 `str`（被销毁的控件） | `hasattr(w, 'winfo_name')` 守卫 |
| **角色列表滚轮失效** | Canvas 的 Enter/Leave 绑定只对 Canvas 本体生效，子控件事件不冒泡 | 改用 `_on_page_scroll` 统一路由 |
| **`_sync_char_table_to_data` + 旧 `_refresh_char_table` 重复定义** | 大块替换后残留旧版方法 | 删除重复代码 |
| **非管理员模式下 SetCursorPos/mouse_event/GetAsyncKeyState 失效** | Windows UIPI 阻止低权限进程向高权限游戏进程注入输入（游戏新版本提升了完整性级别） | 管理员模式运行 GUI；`start.bat` 自动检测提权；`build.py` 加 `--uac-admin` 嵌入 Manifest |

---

## 15. 会话 11（实机调试 & 连招管理优化 & 寻路阻尼）

### 15.1 Portal 检测器简化

| 改动 | 说明 |
|------|------|
| `recognition/portal_detector.py` 移除 ORB | 删除全部 ORB/FLANN/Homography 特征点匹配代码，`detect()` 只走模板匹配。副本出口图标（浅蓝圆圈+中心点）图案简单，ORB 特征点少、易误配到窗外纹理。模板匹配坐标天然在帧内。 |
| `scale_steps` 7 → 11 | 小图标（38-47px）采样密度加倍，匹配更精确 |
| 模板切换 | 3 个预设的 portal_template 改为 `副本出口图标3.png`（不对称版本，h_sym=-0.108，破坏圆形对称性避免镜像误匹配）+ `"color_threshold": 0.6` |

### 15.2 副本出口寻路整治

| 改动 | 文件 | 说明 |
|------|------|------|
| 窗口边界校验 | `dungeon_exit_nav.py` `_do_scan`/`_do_seek`/`_do_center` | `rel_x`/`rel_y` 越界当 lost |
| `rotate_camera_free` sensitivity 400→200 | `controller.py` | 与 NPC 寻路 `rotate_camera` 一致 |
| seek sleep 0.08→0.04 | `dungeon_exit_nav.py` | 与 NPC 对齐 |
| center lost sleep 0.05→0.1 | 同上 | 与 NPC 对齐 |
| `bezier=False` → `bezier=True` | 同上 | `domain_loading` 从安全白名单移除后，按钮恢复贝塞尔路径 |

### 15.3 寻路阻尼（NPC + 副本出口同步）

**机制**：新增 `_reversal_count` 计数器。每次旋转方向反转（lost 6 帧），计数 +1。步长除以 `(reversal_count + 1)`，最小 5。scan 阶段归零。

```
实际步长 = max(5, 原始步长 // (reversal_count + 1))
```

| 反转次数 | 步长 65→ | 步长 30→ |
|:---:|------|------|
| 0 | 65 | 30 |
| 1 | 32 | 15 |
| 2 | 21 | 10 |
| 3+ | 16→ | 7→ |

正常寻路反转数始终为 0，不影响正常流程。仅在 portal/图标在身后的极端场景触发。

**效果验证**：碎星和鬼刃在特定战斗后 portal 起始在身后，触发振荡→阻尼加速收敛。实测碎星从 111s 降至预期 ~15s。

### 15.4 录制功能修复与优化

| 改动 | 说明 |
|------|------|
| `RegisterHotKey` → `GetAsyncKeyState` | 旧版 `RegisterHotKey(NULL,...)` 的 `WM_HOTKEY` 投递到了主线程而非轮询子线程，F5 永远收不到。改为与 Ctrl+Alt+B 相同的 20ms 边沿触发轮询 |
| 移除 F6 热键 | F5 切换开始/停止，F6 冗余（`GetAsyncKeyState` 响应可靠，无需备用） |
| 录制预览 Text → Treeview | 删除 JSON 文本预览区，录制结果直接写入选中的"连招详情" Treeview |
| 移除录制后自动弹窗 | 不再弹出"是否保存"对话框，用户自行决定何时保存 |
| 点"否"不丢失数据 | `_prompt_save_combo` 取消时保留 `_recorded_actions`，后续仍可保存 |

### 15.5 连招元数据补全

| 改动 | 说明 |
|------|------|
| 新增 `source` 字段 | `"录制"`（有 `recorded_at`）/ `"手动配置"`（无 `recorded_at`） |
| `_ensure_combo_metadata()` | 首次加载连招列表时自动补全缺失的 `source` 和 `duration_sec`（从 actions 累加计算），写回文件 |
| 修复来源显示 bug | `source = "" if rec_at else ""`（两分支全空）→ 正确分支 |
| 修复"按住"列显示 bug | 同类型 bug，两分支全空 → `"是"` / `"否"` |
| `ComboEditDialog._save` | 写入 `"source": "手动配置"` + 自动计算 `duration_sec` |
| `_prompt_save_combo` | 写入 `"source": "录制"` |

### 15.6 连招库 UI 增强

| 改动 | 说明 |
|------|------|
| 模糊搜索 | 连招库新增搜索框 + ✕清除按钮，`<KeyRelease>` 实时过滤 |
| 智能排序 | 角色连招下拉框中，包含角色名称的连招排在前面 |
| 使用说明更新 | 增加手动连招说明，去掉 F6 相关描述 |

### 15.7 角色库即时生效

| 改动 | 说明 |
|------|------|
| `_on_charlib_saved()` | 角色库编辑/删除保存后，若预设管理页可见则自动调用 `resolve_characters` 刷新角色列表，无需切预设或重启 |

### 15.8 Bug 修复

| Bug | 根因 | 修复 |
|-----|------|------|
| **录制 F5 无响应** | `RegisterHotKey(NULL)` 消息投递到主线程但轮询在子线程 | 改为 `GetAsyncKeyState` 边沿触发 |
| **录制预览无动作详情** | `rec_result_text` 只显示 JSON 字符串 | 改为直接填充 combo_detail_tree |
| **点击"否"后无法再保存录制结果** | 取消时清空了 `_recorded_actions` | 取消时保留数据，仅在新录制开始时清空 |
| **连招来源列始终为空** | `source = "" if rec_at else ""` 两分支都是空串 | `source = "录制" if rec_at else "手动配置"` |
| **"按住"列始终为空** | 同上模式 | `"是" if hold else "否"` |
| **鬼刃/碎星副本出口长时间振荡** | portal 起始在身后，大步长旋转过冲→反转→反复 | 阻尼机制逐次减半步长，3-5 次反转后收敛 |
