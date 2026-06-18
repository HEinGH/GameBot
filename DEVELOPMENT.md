# GameBot 开发文档

## 1. 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.10+ | 运行时 |
| tkinter / ttk | GUI 界面（~2800 行） |
| OpenCV (cv2) | 模板匹配、图像处理 |
| dxcam | 主要屏幕截图方案（DirectX） |
| mss | 备用屏幕截图方案 |
| pydirectinput | 键盘输入（DirectInput） |
| pywinctl | 游戏窗口查找与激活 |
| Win32 API (ctypes) | `SetCursorPos` + `mouse_event` 鼠标控制、`GetAsyncKeyState` 热键轮询 |
| PyInstaller | 打包为 exe |

**输入方式**：所有鼠标操作通过 Win32 `SetCursorPos`（绝对定位）+ `mouse_event`（点击/移动/滚轮）发送；键盘操作通过 `pydirectinput.keyDown`/`keyUp` 发送。

**管理员权限**：必须以管理员身份运行。Windows UIPI（用户界面特权隔离）会阻止低权限进程向高权限游戏窗口注入输入。`start.bat` 自动提权，打包 exe 内嵌 UAC Manifest。

---

## 2. 架构概览

### 2.1 状态机模式

所有游戏流程由有限状态机（FSM）驱动。每个状态继承 `BaseState`，实现三个生命周期方法：

- `enter(blackboard)` — 进入状态时初始化
- `update(blackboard)` — 每帧调用，执行状态逻辑
- `exit(blackboard)` — 离开状态时清理

状态切换通过 `FSM.transition(name, blackboard)` 完成，自动调用旧状态的 `exit()` 和新状态的 `enter()`。

### 2.2 线程模型

```
┌─────────────────────┐     ┌──────────────────────┐
│  tkinter 主线程      │     │  Bot 后台线程          │
│  - GUI 渲染          │     │  - FSM.update() 循环   │
│  - 日志轮询 (_poll_log)│◄──►│  - 截图 + 识图         │
│  - 热键轮询          │     │  - 输入控制            │
└─────────────────────┘     └──────────────────────┘
           ▲                          ▲
           │        Blackboard        │
           └──────────────────────────┘
                 (线程安全 dict)
```

- **Blackboard**（`core/blackboard.py`）：线程安全的共享上下文，内部使用 `threading.Lock` 保护所有读写操作。存储运行状态、当前帧、角色索引、卡死计数等。
- GUI 主线程与 Bot 线程通过 Blackboard 交换数据，无直接调用。

### 2.3 屏幕截图

- **dxcam**（主要）：DirectX 截图，低延迟，后台模式下可指定 `device_idx`/`output_idx`
- **mss**（备用）：dxcam 不可用时自动回退
- **单例模式**：`ScreenCapture` 使用双检锁单例，全局唯一实例
- 后台线程持续截图，最新帧存入 `_latest_frame`，外部通过 `get_frame()` 获取

### 2.4 模板匹配

基于 OpenCV `matchTemplate`（`TM_CCOEFF_NORMED`），多尺度搜索：

- 缩放范围 0.7x ~ 1.35x，7 个采样步长
- 包含 1.0x 原始尺度
- 超过 1920px 宽度的帧自动降采样
- 可选颜色校验（BGR 直方图相关性）和翻转拒绝（NCC 比较）

### 2.5 输入控制

| 操作 | 实现 |
|------|------|
| 鼠标点击 | `SetCursorPos` 定位 + `mouse_event(LEFTDOWN/LEFTUP)` 长按 150~250ms |
| 鼠标移动 | 隐身模式下贝塞尔曲线平滑移动，非隐身模式瞬移 |
| 视角旋转 | `mouse_event(MOUSEEVENTF_MOVE)` 分 3 步发送相对位移 |
| 键盘 | `pydirectinput.keyDown`/`keyUp`，通过 `_PDI_KEY_MAP` 映射键名 |
| 滚轮 | `mouse_event(MOUSEEVENTF_WHEEL)` |

### 2.6 窗口检测

通过 `pywinctl` 枚举窗口，选取最大可见、非最小化的窗口。优先按 `window_title` 精确匹配，回退搜索过滤 >1000x700px 的窗口。窗口坐标 `_window_rect` 写入 Blackboard，供所有状态使用。

---

## 3. 状态机流程

```
CHARACTER_SELECT → TOWN_NAV → NPC_NAVIGATE → DOMAIN_LOADING → DOMAIN_COMBAT
     ↑                                                              ↓
     │                                                     DUNGEON_EXIT_NAV
     │                                                     ├── 再次挑战 → DOMAIN_LOADING ↻
     │                                                     └── 退出副本 → MAP_LOADING
 TOWN_EXIT ←───────────────────────────────────────────────────────┘
     ├── 切换角色 → CHARACTER_SELECT ↻
     └── 全部完成 → COMPLETE (停止)
```

卡死恢复可从任意状态触发：`任意状态 → STUCK_RECOVERY → TOWN_EXIT`

### 各状态说明

| 状态 | 文件 | 说明 |
|------|------|------|
| `character_select` | `states/character_select.py` | 选人界面：匹配角色头像模板 → 点击 → 等待 → 点击"进入游戏"。支持滚轮翻页（12 角色/6 可见）。 |
| `town_nav` | `states/town_nav.py` | 城镇导航：检测城镇头像确认在城镇 → 执行链式操作（日常按钮 → 副本选择步骤）→ 检测 NPC 图标转入寻路。Alt 键呼出鼠标。 |
| `npc_navigate` | `states/npc_navigate.py` | NPC 寻路：四阶段（scan → seek → center → move），通过旋转视角将 NPC 图标居中，按 W 前进。到达后遍历确认链点击进入副本。同步检测技能栏判断是否已在副本内。 |
| `domain_loading` | `states/domain_loading.py` | 副本加载等待：检测技能栏模板出现即判加载完成，60 秒超时兜底。 |
| `domain_combat` | `states/domain_combat.py` | 副本战斗：播放自定义连招 → 切换兜底连招循环 → 每帧检测角色结算面板 → 点击关闭。 |
| `dungeon_exit_nav` | `states/dungeon_exit_nav.py` | 副本出口寻路：四阶段寻路到出口图标 → 点击"再次挑战"或"退出副本"→ 确认。支持城镇头像 fallback 检测。 |
| `map_loading` | `states/map_loading.py` | 地图加载等待：检测城镇头像出现即判加载完成，45 秒超时兜底。 |
| `town_exit` | `states/town_exit.py` | 城镇退出：打开设置菜单 → 切换角色/退出游戏 → 确认。根据角色索引决定继续循环还是结束。激活游戏窗口确保焦点。 |
| `complete` | `states/complete.py` | 全部完成：释放所有按键，设置 `running=False` 停止 Bot。 |
| `stuck_recovery` | `states/stuck_recovery.py` | 卡死恢复：连按 ESC（每轮 4 次，最多 3 轮）+ 识别设置图标 → 跳转 `town_exit`。累计卡死 3 次则停止 Bot。 |

---

## 4. 关键代码位置

| 模块 | 文件 | 说明 |
|------|------|------|
| 状态机 | `core/fsm.py` | `BaseState` 基类 + `FSM` 状态管理 + `STATE_CN` 中文映射 |
| 共享上下文 | `core/blackboard.py` | 线程安全 dict（`threading.Lock`），存储运行时共享数据 |
| 卡死检测 | `core/watchdog.py` | 基于画面相似度的卡死检测，SSIM 阈值 0.95，15 秒触发 |
| 模板匹配 | `recognition/template.py` | `find_template()` 核心函数，多尺度 + 颜色/翻转校验 |
| 出口检测 | `recognition/portal_detector.py` | `PortalDetector`，副本出口图标的模板匹配检测 |
| 输入控制 | `input/controller.py` | `Controller` 类：`click_at`/`rotate_camera`/`tap_key`/`mouse_scroll` 等 |
| 屏幕截图 | `capture/screen.py` | `ScreenCapture` 单例，dxcam/mss 自动选择 |
| 连招执行 | `combos/executor.py` | `ComboExecutor`，逐帧执行连招动作队列（`deque`） |
| 窗口管理 | `utils/window_manager.py` | `WindowManager`：pywinctl 窗口查找/激活/矩形获取 |
| 虚拟显示器 | `utils/virtual_display.py` | `VirtualDisplayManager`：VDD 驱动管理（后台模式） |
| 反检测 | `utils/antidetection.py` | `HumanDelay`/`MouseTrajectory`/`BehaviorProfile` 拟人化行为 |
| 连招录制 | `utils/macro_recorder.py` | `GetAsyncKeyState` 轮询录制，替代 pynput 钩子 |
| 日志 | `utils/logger.py` | 三路文件日志（常规/错误/崩溃），`RotatingFileHandler` |
| 配置管理 | `config/settings.py` | `Settings` 类、模板解析、角色库序列化/反序列化 |
| GUI | `gui/app.py` | tkinter 主窗口（~2800 行），含预设管理/角色库/连招管理/开发者工具 |
| 打包脚本 | `build.py` | PyInstaller 配置，`--uac-admin` 内嵌管理员 Manifest |
| 启动脚本 | `start.bat` | 自动检测提权并启动 |

---

## 5. 数据格式

### 5.1 预设 JSON (`config/presets/*.json`)

```jsonc
{
  "description": "预设描述",
  "window_title": "游戏窗口标题",

  // 全局模板
  "enter_game_template": "进入游戏.png",
  "skill_bar_template": "skill_bar.png",
  "result_screen_template": "result_clear.png",
  "rechallenge_template": {"template": "再次挑战.png", "threshold": 0.8},
  "exit_domain_template": {"template": "退出副本.png", "threshold": 0.8},
  "portal_template": {"template": "副本出口图标3.png", "threshold": 0.74},

  // 城镇导航
  "town_nav": {
    "avatar_template": "avatar.png",
    "alt_for_mouse": true,          // Alt 键呼出鼠标
    "domain_select_steps": [         // 链式操作步骤
      "日常按钮.png",
      "深渊1.png",
      {"template": "深渊3.png", "threshold": 0.55}
    ],
    "confirm_enter_template": ["确认.png"],  // 确认进入链
    "npc_marker_template": {"template": "npc寻路图标.png", "threshold": 0.8}
  },

  // 退出流程
  "town_exit": {
    "settings_template": "设置按钮.png",
    "switch_character_template": "切换角色.png",
    "exit_game_template": "退出游戏.png",
    "confirm_exit_template": "确认.png"
  },

  // 角色列表（引用格式，运行时通过 resolve_characters 解析为完整角色）
  "characters": [
    {"name": "镰卫", "runs": 3, "combo": "镰卫_深渊2"},
    {"name": "赏金", "runs": 3, "combo": "赏金_深渊2"}
  ],

  // 预设级兜底连招（combo 文件名引用）
  "fallback_combo": "深渊_兜底",

  // 运行参数（GUI 记忆）
  "char_count": 1,
  "char_start": 1,
  "stealth": true,
  "background": false,
  "exit_after_done": false
}
```

**模板字段格式**：
- 纯字符串：`"xxx.png"`（使用默认阈值 0.65）
- 对象格式：`{"template": "xxx.png", "threshold": 0.55, "color_threshold": 0.7, "reject_flip": true}`

### 5.2 角色库 JSON (`config/characters/*.json`)

```jsonc
{
  "name": "鬼刃",
  "portrait_template": {         // 选人界面头像
    "template": "鬼刃\\鬼刃选人界面头像.png",
    "threshold": 0.7,
    "reject_flip": true,
    "color_threshold": 0.7
  },
  "skill_bar_template": {       // 技能栏（副本加载检测）
    "template": "鬼刃\\鬼刃技能栏.png",
    "threshold": 0.75,
    "reject_flip": true
  },
  "result_screen_template": {   // 结算画面
    "template": "鬼刃\\鬼刃结算画面.png",
    "threshold": 0.7,
    "reject_flip": true
  },
  "avatar_template": {          // 城镇头像
    "template": "鬼刃\\鬼刃城镇头像.png",
    "threshold": 0.65,
    "reject_flip": true,
    "color_threshold": 0.7
  }
}
```

每个模板字段可以是纯字符串或包含 `threshold`/`color_threshold`/`reject_flip` 的对象。

### 5.3 连招 JSON (`combos/*.json`)

```jsonc
{
  "name": "鬼刃_深渊",
  "source": "手动配置",           // "录制" 或 "手动配置"
  "recorded_at": null,           // 录制时间戳（录制来源）
  "duration_sec": 18.7,          // 总时长（自动计算）
  "actions": [
    {
      "keys": ["2"],             // 按键列表（支持多键同按）
      "duration": 0.31,          // 按键持续时间（秒）
      "delay_before": 1.0,      // 按键前延迟
      "delay_after": 1.0,       // 按键后延迟
      "hold": false,            // 是否长按
      "repeat": 1               // 重复次数（加载时展开为独立动作）
    }
  ]
}
```

---

## 6. 模板匹配系统

核心函数：`recognition/template.py:find_template()`

### 6.1 多尺度匹配

- 缩放范围：0.7x ~ 1.35x，7 个等距步长
- 始终包含 1.0x 原始尺度
- 覆盖窗口化（~0.67x）到全屏（~1.33x）全尺寸
- 超过 1920px 宽度的帧自动降采样后匹配

### 6.2 颜色校验

- 通过 `_color_registry` 字典注册模板的颜色阈值
- 匹配成功后计算 ROI 与模板的 BGR 三通道直方图相关性
- 相关性低于阈值则拒绝匹配
- ROI 自动 resize 到模板尺寸以确保直方图分箱一致
- 直方图缓存在 `_hgram_cache` 中

### 6.3 翻转拒绝

- 通过 `_flip_registry` 集合注册需要翻转检查的模板
- 匹配成功后计算 ROI 与原始模板的 NCC 和翻转模板的 NCC
- 若翻转 NCC > 原始 NCC，拒绝匹配（防止对称图案误匹配）

### 6.4 边缘腐蚀

- 面积 < 3000px 的小模板，灰度图执行 `cv2.erode`（3x3 核，1 次迭代）
- 去除抗锯齿边缘过渡区，提升小图标匹配稳定性

### 6.5 自动更新

- `auto_update=True` 且面积 < 3000px 且置信度 > 0.75 时
- 用截图 ROI 覆盖原始模板文件
- 使模板适应当前游戏分辨率/渲染状态

### 6.6 边缘区域拒绝

- NPC 寻路、出口检测、按钮检测中，匹配位置在帧 5% 边缘区域内的结果被拒绝
- 防止窗口边缘 UI 元素误匹配

---

## 7. 寻路系统

NPC 寻路（`states/npc_navigate.py`）和副本出口寻路（`states/dungeon_exit_nav.py`）共享几乎相同的逻辑。

### 7.1 四阶段流程

| 阶段 | 行为 |
|------|------|
| `scan` | 全屏搜索目标图标，未找到则旋转视角扫描 |
| `seek` | 图标在窗口下半区，逐帧旋转镜头将图标转到上半区 |
| `center` | 比例旋转水平居中，偏移 < 2% 帧宽视为已居中 |
| `move` | 按住 W 前进，每 2 帧检查偏移，偏离 > 6% 则回 center 重校 |

### 7.2 自适应旋转步长表

基于水平偏移比例 `h_ratio` 分档：

| h_ratio | 步长（度） |
|---------|-----------|
| > 40% | 65 |
| > 25% | 50 |
| > 12% | 30 |
| > 6% | 18 |
| > 3% | 10 |
| ≤ 3% | 5 |

### 7.3 阻尼机制

`_reversal_count` 追踪方向翻转次数，两种触发方式：

1. **丢失反转**：图标丢失 6 帧后方向反转
2. **可见方向翻转**：目标可见时 `_search_dir` 或 `_do_rotate` 的偏移符号翻转

实际步长 = `max(3, 原始步长 // (reversal_count + 1))`

| 反转次数 | 65° → | 30° → |
|:---:|------|------|
| 0 | 65 | 30 |
| 1 | 32 | 15 |
| 2 | 21 | 10 |
| 3+ | 16→ | 7→ |

`scan` 阶段不重置阻尼计数，仅在 `enter()` 真正进入状态时归零。

### 7.4 位置连续性校验

- 匹配位置与 `_last_pos` 水平跳变 > 30% 帧宽 → 拒绝
- 防止窗口边缘 UI 按钮误匹配

### 7.5 Soft-fallback

- 主阈值未匹配到但已有 `_last_pos` 时，降阈值至 0.65 重试
- 通过连续性校验后接受
- 解决目标转到暗色背景时置信度临时下降的问题

### 7.6 差异

| 特性 | NPC 寻路 | 出口寻路 |
|------|---------|---------|
| 旋转方法 | `rotate_camera`（左键拖拽） | `rotate_camera_free`（无按键，避免触发攻击） |
| 目标检测 | `find_template` NPC 图标 | `PortalDetector` 出口图标 |
| 到达后 | 遍历确认进入链点击 | 点击再次挑战/退出副本按钮 |
| 技能栏检测 | 有（判断是否已在副本） | 无 |

---

## 8. 开发与调试

### 8.1 运行

```bash
# GUI 模式（推荐，需管理员终端）
python gui.py

# CLI 模式
python main.py --list                    # 列出预设
python main.py -p default -c 2           # 指定预设运行
python main.py --record-combo my_combo   # 录制连招
```

### 8.2 打包

```bash
python build.py
```

PyInstaller `--onefile` + `--uac-admin`（内嵌管理员 Manifest）+ `--noconsole`。自动打包 `templates/`、`config/`、`combos/` 目录。

### 8.3 日志

| 文件 | 级别 | 说明 |
|------|------|------|
| `logs/game_bot.log` | INFO | 常规运行日志 |
| `logs/game_bot_error.log` | ERROR | 错误日志 |
| GUI 日志面板 | INFO/DEBUG | 实时显示，最大 500 行，可切换 DEBUG 级别 |

### 8.4 调试资源

| 资源 | 说明 |
|------|------|
| `debug/` 目录 | 卡死检测截图（`stuck_HHMMSS_N.png`） |
| 模板批量测试 | 开发者工具页按钮，截图 → 遍历所有角色模板 → 输出置信度 |
| 状态选择器 | 开发者工具页，可从任意状态启动 Bot（跳过前置流程） |

### 8.5 关键中文日志格式

```
状态切换: 副本战斗 → 副本出口寻路
识图成功: xxx.png 位置(x,y) 置信度=0.xxx
卡死检测 #N | 相似度=0.xxx
```

---

## 9. 打包

### 9.1 构建命令

```bash
python build.py
```

### 9.2 打包配置（`build.py`）

- `--onefile`：单文件 exe
- `--uac-admin`：内嵌管理员 Manifest，运行时自动请求提权
- `--noconsole`：无控制台窗口
- `--add-data`：打包 `templates/`、`config/`、`combos/` 目录
- `--hidden-import`：显式声明全部隐式导入（pydirectinput、cv2、dxcam、mss、pywinctl、plyer、tkinter、ctypes 等）

### 9.3 自动提权

`start.bat` 检测当前权限，非管理员时通过 `ShellExecute` runas 自动提权后启动。

### 9.4 打包后路径

打包后 `settings.json` 写入 exe 所在目录（通过 `config/settings.py:get_writable_dir()`），而非 PyInstaller 临时解压目录。

---

## 10. 技术难点与解决方案

| 难点 | 解决方案 |
|------|---------|
| 全屏游戏下 pynput 钩子失效 | 热键改用 `GetAsyncKeyState` 20ms 轮询 + 边沿触发 |
| `pydirectinput.moveRel` 窗口边缘被 ClipCursor 钳制 | `rotate_camera` 改为 Win32 `mouse_event(MOUSEEVENTF_MOVE)` 分步发送 |
| 游戏拦截软件鼠标点击 | `SetCursorPos` + `mouse_event` 长按 150~250ms 绕过 |
| 中文文件名 imread 失败 | `np.fromfile` + `cv2.imdecode` 替代 `cv2.imread` |
| 中文文件名 imwrite 失败 | `cv2.imencode` + `open()` 手动写入替代 `cv2.imwrite` |
| 小模板置信度波动 | 边缘腐蚀 + `reject_flip` + `auto_update` 三重防御 |
| 寻路震荡（旋转过冲） | 可见方向翻转检测 + 阻尼步长衰减 + 最小 3° 步长 |
| 同名启动器干扰窗口查找 | 选最大可见非最小化窗口，回退过滤 > 1000x700px |
| NPC 图标被窗口边缘 UI 误匹配 | 位置连续性校验（跳变 > 30% 帧宽拒绝）+ 5% 边缘区域拒绝 |
| 角色列表超一屏 | 窗口相对坐标 + 滚轮翻页（-600 = 5 格/次） |
| Windows UIPI 阻止输入注入 | 必须管理员运行 + PyInstaller `--uac-admin` + `start.bat` 自动提权 |
| 贝塞尔移动被游戏解释为视角转动 | 副本内按钮点击传 `bezier=False` |
| domain_loading 中鼠标晃动转镜头 | 从隐身安全白名单移除 `domain_loading` |
