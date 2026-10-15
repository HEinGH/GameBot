## v1.1 — 日志优化 & 寻路自愈 & 颜色校验配置

### 日志系统重构
- **日分文件**：日志文件名包含日期（`game_bot_2026-07-11.log`），每天自动切换新文件
- **DEBUG 全量记录**：文件记录所有 DEBUG 级别日志，方便排查问题
- **错误分离**：WARNING 及以上级别单独写入 `game_bot_error_{date}.log`
- **GUI 显示过滤**：控制台保持 INFO 级别；GUI 面板 DEBUG 勾选框仅控制显示，不影响文件记录
- **崩溃日志**：未捕获异常写入 `game_bot_crash_{date}.log`，含完整堆栈和环境信息

### 副本出口寻路自愈系统（4 层）
- **L0a 盲扫**：出口图标连续丢失 10 帧后启动盲扫，每 3 帧旋转 12°，每 8 步换向，逐步覆盖全视野
- **L0b 超时回扫**：seek/move 阶段丢失 > 20 帧时回 scan 重新搜索，而非直接放弃到 buttons
- **L1 连续性锁自愈**：丢失 ≥ 5 帧时触发裸检测（跳过连续性过滤），连续 3 帧同位置确认后重置定位锁，避免持久误拒
- **L2 自适应降阈值**：丢失 ≥ 30 帧降 0.05、≥ 50 帧降 0.10（最低 0.45），暗色场景自动放宽匹配
- NPC 寻路同步应用 L1 自愈逻辑

### 链式步骤支持颜色校验
- 城镇导航操作链和确认进入链每行新增 **"形"/"色"** 两个置信度配置
  ```
  步骤 1 → [临界3.png] 形 [0.65] 色 [0.00] [浏览]
  ```
- `形`（形状置信度）：原有阈值，控制模板匹配精度
- `色`（颜色置信度）：新增，设为 > 0 时启用 BGR 直方图颜色校验（0.00 关闭）
- 用于区分同形异色图标（如确认界面左中右三个形状相同但颜色不同的按钮）
- 配置自动持久化到预设 JSON，`parse_template_chain` 注入全局颜色注册表

### 其他修正
- **scale_range 全项目统一**：`npc_navigate._find()` 和 `portal_detector._match_template()` 的 scale_range/scale_steps 统一为 v1.0 标准 `(0.7, 1.35) / 7 步`

---

### 文件变更
| 文件 | 操作 |
|------|------|
| `utils/logger.py` | 新增 `DailyRotatingFileHandler`，重构 `setup_logger`，日分文件 + DEBUG 分离 |
| `states/dungeon_exit_nav.py` | 新增 L0a/L0b/L1/L2 完整自愈系统（+~80 行） |
| `states/npc_navigate.py` | L1 连续性锁自愈同步 + `_find()` scale_range 统一 |
| `recognition/portal_detector.py` | scale_steps 统一 |
| `gui/app.py` | `ChainStepList` 新增颜色阈值列 + `_on_log_level_change` 只改 QueueHandler |
| `main.py` | 默认日志级别改为 DEBUG |
