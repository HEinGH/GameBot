# GameBot — 自动化清体力脚本

基于图像识别 + 状态机的游戏日常副本自动化脚本。支持多角色连招配置、兜底连招轮转、防检测隐身模式、连招录制。提供完整的 tkinter 图形界面。

## 快速开始

```bash
pip install -r requirements.txt
python gui.py
```

在 GUI 中配置预设、角色和模板后，点击启动即可运行。

## 状态机流程

```
CHARACTER_SELECT → TOWN_NAV → NPC_NAVIGATE → DOMAIN_LOADING → DOMAIN_COMBAT
     ↑                                                    ↓
     │                                           DUNGEON_EXIT_NAV
     │                                           ├── 再次挑战 → DOMAIN_LOADING ↻
     │                                           └── 退出副本 → MAP_LOADING
 TOWN_EXIT ←──────────────────────────────────────────────┘
     │
     ├── 切换角色 → CHARACTER_SELECT ↻
     └── 全部完成 → COMPLETE (停止)
```

| 状态 | 说明 |
|------|------|
| `character_select` | 识图选角色头像 → 点进入游戏；支持滚轮翻页 |
| `town_nav` | 确认城镇头像 → Alt 显示鼠标 → 操作链（日常→副本→挑战）→ 检测 NPC 图标 |
| `npc_navigate` | 基于 NPC 头顶图标引导寻路（scan→seek→center→move）→ 确认进入副本 |
| `domain_loading` | 等待加载 → 识图技能栏确认已进入副本 |
| `domain_combat` | 执行角色连招 → 兜底连招轮转 → 识图结算面板 → 点击关闭 |
| `dungeon_exit_nav` | 识图出口光圈引导寻路 → 到达出口 → 点击再次挑战/退出 |
| `map_loading` | 等待地图加载 → 识图角色头像确认回城 |
| `town_exit` | ESC → 设置 → 切换角色 or 退出游戏 |
| `complete` | 全部角色完成 → 停止 |
| `stuck_recovery` | 卡死检测触发 → 执行恢复操作 → 回 character_select |

## GUI 界面

左侧导航栏：

| 页面 | 功能 |
|------|------|
| **运行控制** | 预设选择、执行角色数、隐身/后台开关、启动/停止、运行状态日志 |
| **预设管理** | 预设全局配置（模板路径、操作链、退出流程）、角色列表（添加/编辑/删除/排序）、兜底连招编辑 |
| **开发者工具** | 跳过刷本测试切换角色流程 |
| **连招录制（待测试）** | 录制键盘鼠标操作为连招文件 |
| **截图工具** | 全屏截图 → 裁剪区域 → 保存模板到 `templates/` |
| **全局设置** | FPS 限制、捕获方式、卡死检测参数 |

日志直接输出到"运行控制"页面的运行状态区域，支持 INFO/DEBUG 级别切换。

## 预设配置结构

每个预设是一个 JSON 文件，存放在 `config/presets/`：

```json
{
  "description": "",
  "window_title": "",
  "char_count": 1,
  "stealth": false,
  "background": false,
  "enter_game_template": "",
  "rechallenge_template": "",
  "exit_domain_template": "",
  "portal_template": "",
  "town_nav": {
    "domain_select_steps": [],
    "alt_for_mouse": true,
    "confirm_enter_template": "",
    "npc_marker_template": ""
  },
  "town_exit": {
    "settings_template": "",
    "switch_character_template": "",
    "exit_game_template": "",
    "confirm_exit_template": ""
  },
  "fallback_combos": [],
  "characters": [
    {
      "name": "",
      "portrait_template": "",
      "skill_bar_template": "",
      "result_screen_template": "",
      "avatar_template": "",
      "runs": 4,
      "combos": [
        {"keys": ["1"], "duration": 0.1, "delay_after": 0.5}
      ],
      "fallback_combos": null
    }
  ]
}
```

### 连招动作字段

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `keys` | string[] | 必填 | 按键组合（多键同时按下） |
| `duration` | number | 0.1 | 按键持续时间（秒） |
| `delay_before` | number | 0 | 动作前延迟 |
| `delay_after` | number | 0 | 动作后延迟 |
| `hold` | boolean | false | 是否按住不放 |
| `repeat` | number | 1 | 重复次数 |

## 命令行参数

```bash
python main.py --list                  # 列出所有预设
python main.py -p default -c 2          # 用 default 预设刷 2 个角色
python main.py -p default --stealth     # 隐身模式
python main.py --record-combo my_combo  # 录制连招
```

| 参数 | 说明 |
|------|------|
| `-p, --preset` | 预设名称 |
| `-c, --characters` | 执行角色数 |
| `--stealth` | 隐身模式（类人延迟+鼠标轨迹） |
| `--background` | 后台模式（自动管理窗口焦点） |
| `--window-title` | 游戏窗口标题关键词 |
| `--secondary-monitor` | 将窗口移到指定显示器 |
| `--record-combo` | 录制键盘鼠标为连招 JSON |

## 项目结构

```
game_bot/
├── gui.py / gui.pyw             # GUI 入口
├── main.py                      # CLI 入口
├── build.py                     # PyInstaller 打包
├── start.bat                    # 双击启动
│
├── config/
│   ├── settings.py              # 全局设置（单例）
│   ├── settings.json            # 用户保存的设置（仅记忆 last_preset）
│   └── presets/                 # 预设 JSON 文件
│
├── core/
│   ├── fsm.py                   # 状态机（BaseState + FSM + 中文状态名映射）
│   ├── blackboard.py            # 线程安全上下文
│   └── watchdog.py              # 卡死检测（SSIM 画面相似度）
│
├── capture/
│   └── screen.py                # 屏幕捕获（dxcam/mss，单例）
│
├── recognition/
│   ├── template.py              # matchTemplate 多尺度模板匹配
│   ├── npc_detector.py          # ORB + FLANN NPC 头顶图标检测
│   └── portal_detector.py       # ORB + 模板匹配 出口光圈检测
│
├── input/
│   └── controller.py            # Win32 API 输入模拟（SetCursorPos + mouse_event + pydirectinput）
│
├── combos/
│   ├── executor.py              # 连招执行器
│   └── *.json                   # 录制的连招文件
│
├── states/                      # 状态机状态
│   ├── character_select.py
│   ├── town_nav.py
│   ├── npc_navigate.py
│   ├── domain_loading.py
│   ├── domain_combat.py
│   ├── dungeon_exit_nav.py
│   ├── map_loading.py
│   ├── town_exit.py
│   ├── complete.py
│   └── stuck_recovery.py
│
├── gui/
│   └── app.py                   # tkinter GUI（主窗口 + 多个对话框类）
│
├── utils/
│   ├── antidetection.py         # 反检测（HumanDelay / MouseTrajectory / BehaviorProfile）
│   ├── logger.py                # 三路日志（常规/错误/崩溃轮转）
│   ├── window_manager.py        # pywinctl 窗口管理
│   └── macro_recorder.py        # GetAsyncKeyState 轮询连招录制
│
└── templates/                   # 截图模板 PNG 文件
```

## 技术要点

- **状态机**：每个状态继承 `BaseState`，通过 `FSM.transition()` 切换
- **线程模型**：tkinter 主线程 + Bot 后台线程，通过 `Blackboard`（加锁）共享状态
- **输入模拟**：鼠标用 Win32 `SetCursorPos` + `mouse_event`（绕过游戏反模拟），键盘用 `pydirectinput`
- **图像识别**：OpenCV `matchTemplate` 多尺度（0.5~1.5，11 步）+ ORB 特征匹配
- **窗口定位**：`pywinctl` 搜索最大可见非最小化窗口，寻路基于窗口相对坐标
- **日志**：三路文件轮转 + GUI 实时输出
