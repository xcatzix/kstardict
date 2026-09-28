# kstardict DBus 全局关闭浮窗方案

> 适用版本：kstardict15（Python/Tkinter 浮窗 + KRunner DBus 插件）
> 文档目的：解决「浮窗无法获得焦点，导致 `<Escape>` 绑定失效」的问题——把关闭浮窗的逻辑暴露为一个 **DBus 方法**，由桌面环境的**全局快捷键**触发，从而在任何焦点状态下都能关闭浮窗。

---

## 1. 背景与问题

- Tkinter 的 `widget.bind("<Escape>", ...)` 只在 Tk 应用自身拥有键盘焦点时触发。
- kstardict 的翻译浮窗是 `overrideredirect(True)` 的无装饰窗口，**无法获得焦点**，因此 `<Escape>` 与 `<FocusOut>` 绑定基本不生效。
- 需求：无论焦点在何处，按一个全局快捷键即可关闭浮窗，并执行清理逻辑（`_query_tr()` / `_dismiss_krunner()`）。

## 2. 设计思路

| 组件 | 职责 |
|------|------|
| `KStarDictPopupService`（新增 DBus 服务） | 暴露 `ClosePopup` 方法，调用统一关闭函数 |
| 全局快捷键（KDE/GNOME/xbindkeys） | 捕获按键 → 调用 `dbus-send` → 触发 `ClosePopup` |
| 统一关闭函数 `kstardict_close_popup()` | 销毁浮窗 + 重置状态 + 清理 KRunner |

> **Wayland 提示**：Wayland 协议禁止客户端直接监听全局按键，因此本方案（桌面环境全局快捷键 → DBus）是目前唯一合规、跨 X11/Wayland 均有效的做法。

---

## 3. Python 端：暴露 DBus 服务

在 `kstardict15_final.py` 中新增如下代码（建议放在全局状态定义之后、`StarDictRunner` 类之前）。

### 3.1 统一关闭函数

将原来散落在 `_close` / `_dismiss_krunner` 中的关闭逻辑抽为独立函数，供 DBus 方法与内部按钮统一调用：

```python
def kstardict_close_popup():
    """供 DBus / 全局热键 / 内部按钮统一调用的关闭入口。"""
    global _active_popup
    if _active_popup is None:
        return
    try:
        _active_popup.destroy()
    except tk.TclError:
        pass
    _active_popup = None
    _query_tr()
    _dismiss_krunner(_pending_word)
```

### 3.2 DBus 服务类

```python
import dbus
import dbus.service

class KStarDictPopupService(dbus.service.Object):
    """通过 DBus 提供浮窗控制接口（当前：关闭浮窗）。"""

    def __init__(self):
        bus_name = dbus.service.BusName(
            "org.kstardict.popup", bus=dbus.SessionBus()
        )
        super().__init__(bus_name, "/org/kstardict/popup")

    @dbus.service.method("org.kstardict.popup")
    def ClosePopup(self):
        """关闭当前翻译浮窗并执行 KRunner 清理。"""
        kstardict_close_popup()
        return True
```

### 3.3 启动服务

在 `StarDictRunner.__init__` 末尾（或 `if __name__ == "__main__"` 块中，DBus 主循环启动之后）实例化服务：

```python
from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)
popup_service = KStarDictPopupService()
```

> 由于项目已使用 `dbus-python` + `DBusGMainLoop` + `GLib.MainLoop`，新增的 `KStarDictPopupService` 会复用同一主循环，**无需额外线程**。

---

## 4. 关闭脚本 `kstardict-close.sh`

将下面脚本保存为 `kstardict-close.sh` 并赋予可执行权限（`chmod +x`）：

```bash
#!/bin/bash
# 通过 DBus 调用 kstardict 关闭浮窗方法
dbus-send --session \
  --dest=org.kstardict.popup \
  /org/kstardict/popup \
  org.kstardict.popup.ClosePopup
```

> 路径建议：`~/.local/bin/kstardict-close.sh`（确保 `~/.local/bin` 在 `PATH` 中，或直接使用绝对路径配置快捷键）。

---

## 5. 桌面环境：绑定全局快捷键

### 5.1 KDE Plasma

1. 打开 **系统设置 → 快捷键（Shortcuts）→ 自定义快捷键**。
2. **编辑 → 新建 → 全局快捷键 → 命令/URL**。
3. 名称：`KStarDict Close Popup`。
4. **触发器**：点击「无」，按下组合键（推荐 `Ctrl+Shift+Q` 或 `Super+Esc`，**避免裸 `Esc` 与其他应用冲突**）。
5. **动作**：命令/URL 填脚本的**绝对路径**，如 `/home/你的用户名/.local/bin/kstardict-close.sh`。
6. 点击 **应用**。

### 5.2 GNOME

**设置 → 键盘 → 自定义 Shortcuts → +**，名称 `KStarDict Close Popup`，命令填脚本绝对路径，快捷键设为上述组合键。

### 5.3 X11 通用（xbindkeys）

在 `~/.xbindkeysrc` 中添加：

```
# 关闭 kstardict 浮窗（组合键，避免抢占裸 Esc）
"dbus-send --session --dest=org.kstardict.popup /org/kstardict/popup org.kstardict.popup.ClosePopup"
  Control+Shift+q
```

> ⚠️ 不建议将裸 `Escape` 注册为全局快捷键：会抢占其他应用的 Esc（取消/退出全屏等）行为。推荐使用带修饰键的组合（如 `Ctrl+Shift+Q`、`Super+Esc`）。

---

## 6. 与现有代码的整合建议

1. **删除失效绑定**：浮窗无焦点，`popup.bind("<Escape>", ...)` 与 `popup.bind("<FocusOut>", ...)` 可移除或保留为无副作用的兜底；真正的关闭入口改为 `kstardict_close_popup()`。
2. **统一调用点**：
   - 浮窗内「关闭/字典名/滚动指示器」等按钮 → 调用 `kstardict_close_popup()`。
   - DBus `ClosePopup` 方法 → 调用 `kstardict_close_popup()`。
   - 全局快捷键脚本 → DBus → `kstardict_close_popup()`。
3. **原 `_make_close` 函数**：可简化为对 `kstardict_close_popup()` 的包装，保留焦点判断逻辑作为兜底即可（因 `event.type == "10"` 字符串比较问题已在前面修复讨论中处理）。

---

## 7. 验证步骤

1. 启动 kstardict 插件（确保 `dbus-python` / `PyGObject` 已安装）。
2. 在 KRunner 输入 `tr hello.` 触发浮窗。
3. 将焦点切到其他应用窗口。
4. 按下配置的全局快捷键（如 `Ctrl+Shift+Q`）。
5. 预期：浮窗关闭、KRunner 输入框重置为 `tr `、状态栏清理完成。
6. 命令行手动验证 DBus 连通性：
   ```bash
   dbus-send --session --dest=org.kstardict.popup \
     /org/kstardict/popup org.kstardict.popup.ClosePopup
   ```

---

## 8. 依赖与兼容性

| 项目 | 说明 |
|------|------|
| Python | ≥ 3.8 |
| dbus-python | `pip install dbus-python`（系统包 `python3-dbus` 推荐） |
| PyGObject (gi) | `pip install PyGObject`（系统包 `python3-gi` 推荐） |
| Tkinter | 通常随 Python 自带 |
| X11 / Wayland | 本方案在两者下均可工作（全局快捷键由桌面环境提供） |

---

## 附录 A：完整补丁示例（可直接合入 `kstardict15_final.py`）

```python
# ===== 新增：统一关闭入口 =====
def kstardict_close_popup():
    global _active_popup
    if _active_popup is None:
        return
    try:
        _active_popup.destroy()
    except tk.TclError:
        pass
    _active_popup = None
    _query_tr()
    _dismiss_krunner(_pending_word)

# ===== 新增：DBus 浮窗控制服务 =====
class KStarDictPopupService(dbus.service.Object):
    def __init__(self):
        bus_name = dbus.service.BusName(
            "org.kstardict.popup", bus=dbus.SessionBus()
        )
        super().__init__(bus_name, "/org/kstardict/popup")

    @dbus.service.method("org.kstardict.popup")
    def ClosePopup(self):
        kstardict_close_popup()
        return True

# ===== 在 main 块中启动（复用既有 DBusGMainLoop）=====
# DBusGMainLoop(set_as_default=True)  # 已存在
popup_service = KStarDictPopupService()
```

---

**文档维护**：Xcatzix · kstardict 项目
