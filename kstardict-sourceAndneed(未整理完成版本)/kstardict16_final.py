#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kstardict16_final.py —— KRunner 英文→中文翻译字典（DBus 全局关闭浮窗版）
依赖：KRunner + QStarDict + Python3 + Tkinter + feh + wkhtmltoimage(可选)
功能：
  - tr wOrd. 触发翻译，浮窗显示译文（三类颜色）
  - 翻译结果同时在 KRunner 结果列表中显示（标题=首行释义，subtext=其余释义）
  - 翻译结果 HTML → PNG（900px 宽，黑底白字），存 ~/.cache/kstardict/{word}.png
  - 底部 statusbar：[字典名][word][天气][时间][▽]，元素上下居中
  - 天气缓存 ~/.cache/weatherWTTR/W_state.json（文本 + 下次请求时间，默认+30分钟）
  - 天气/word 文件更新时先删除旧文件再覆写，保证内容最新
  - 点击 bar 上 word → feh 800x500 --start-at 打开对应 PNG（可翻页）
  - 点击 bar 上天气 → feh 打开 ~/.cache/weatherWTTR/W.png
  - 鼠标悬浮提示：word→"点击Open..."，指示器→"滚轮可下拉内容..."，字典名→"点击打开帮助文档"
  - 点击字典名 / 输入 tr ? → 生成 HELP.png（docstring 内容）并存于 ~/.cache/kstardict/
  - 新增 DBus 服务 org.kstardict.popup，暴露 ClosePopup/Ping 方法，
    支持桌面环境全局快捷键（Esc/Ctrl+Shift+Q 等）通过 DBus 关闭浮窗
  - 点击 KRunner 结果项：仅关闭 KRunner，不再额外弹窗（弹窗由 Match 阶段统一触发）
Author: Xcatzix
"""

import re
import os
import subprocess
import threading
import queue
import time
import json
import urllib.request
import html as _html_mod
from dbus.mainloop.glib import DBusGMainLoop
import dbus
import dbus.service
from gi.repository import GLib
import tkinter as tk

SERVICE = "com.yourname.krunner-stardict"
PATH = "/com/yourname/krunner_stardict"
QSTAR = "org.qstardict.dbus"
QOBJ = "/qstardict"
TRIGGER = "tr"

# ---------- 缓存目录配置 ----------
CACHE_KSTARDICT_DIR = os.path.expanduser("~/.cache/kstardict")
WEATHER_CACHE_DIR = os.path.expanduser("~/.cache/weatherWTTR")
WEATHER_STATE_FILE = os.path.join(WEATHER_CACHE_DIR, "W_state.json")
WEATHER_CACHE_PNG = os.path.join(WEATHER_CACHE_DIR, "W.png")
WEATHER_FETCH_INTERVAL = 1800  # 30 分钟（秒）
WEATHER_CITY = "Nuremberg"

# ---------- 浮窗视觉配置 ----------
BG_COLOR = "#97e3d3"
FG_COLOR = "#222222"
FONT_FAMILY = "Ligconsolata Regular"
FONT_SIZE = 13
BORDER_COLOR = "#f93301"
BORDER_WIDTH = 2
SHADOW_COLOR = "#454d4f"
SHADOW_MARGIN = 1
SHADOW_LEVELS = [
    ("#cef7f7", 1),
    ("#b6ced4", 1),
    ("#344240", 1),
    ("#484d4f", 0),
]
LINE_SPACING = 6
POPUP_HEIGHT = 280
POPUP_WIDTH = 834
SCROLLBAR_WIDTH = 18
SCROLLBAR_COLOR = "#5a6062"
SCROLLBAR_BG = BG_COLOR

# --- 状态栏配置 ---
STATUSBAR_HEIGHT = 38
STATUSBAR_BG = "#40434f"
BOX_HIGHLIGHT_BG = STATUSBAR_BG
BOX_HIGHLIGHT_COLOR = "#5a6062"
BOX_HIGHLIGHT_THICKNESS = 4
DICT_NAME_FG = "#1dd357"
DICT_NAME_SIZE = 9
SCROLL_INDICATOR_TEXT = "\u25bc"
# bar 内各元素方框统一高度
BOX_FIXED_HEIGHT = 34

# ---------- 全局状态 ----------
_pending_word = ""
_lock = threading.Lock()
_popup_queue = queue.Queue()
_tk_root = None
_active_popup = None
_popup_lock = threading.Lock()
_last_query = ""
MIN_WORD_LEN = 2
_bcloser = 0

# qdbus 探测缓存
_QDBUS = None

# 模块 docstring 缓存（帮助文档内容）
_HELP_DOC = __doc__ or ""


def _detect_cjk_font(size=13):
    """探测系统中存在的中文字体，优先选等宽字体"""
    candidates = [
        # 覆盖Debian/Ubuntu/Arch/Fedora等主流发行版
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.otf",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                from PIL import ImageFont

                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # 所有字体都找不到时，回退到PIL默认字体
    from PIL import ImageFont

    return ImageFont.load_default()


# ============================================================
# 天气缓存：~/.cache/weatherWTTR/W_state.json
# 记录 {text, next_fetch_at}，next_fetch_at = 当前请求时间 + 30分钟
# ============================================================


def _load_weather_state():
    try:
        with open(WEATHER_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("text", ""), float(data.get("next_fetch_at", 0.0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return "", 0.0


def _save_weather_state(text, next_fetch_at):
    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    tmp = WEATHER_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"text": text, "next_fetch_at": next_fetch_at},
            f,
            ensure_ascii=False,
            indent=2,
        )
    os.replace(tmp, WEATHER_STATE_FILE)


def _should_fetch_weather():
    _, next_fetch_at = _load_weather_state()
    return time.time() >= next_fetch_at


def _download_weather_png():
    """下载/覆写 W.png（先删除旧文件再下载，保证更新）。"""
    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    try:
        if os.path.exists(WEATHER_CACHE_PNG):
            os.remove(WEATHER_CACHE_PNG)
    except Exception:
        pass
    try:
        url = f"https://wttr.in/{WEATHER_CITY}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            with open(WEATHER_CACHE_PNG, "wb") as f:
                f.write(resp.read())
    except Exception as e:
        print(f"[kstardict] _download_weather_png error: {e}")


def _fetch_weather_now():
    """立即请求 wttr.in，成功后更新状态文件（文本+图片均覆写）。"""
    try:
        url = f"https://wttr.in/{WEATHER_CITY}?format=3"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            text = resp.read().decode("utf-8").strip()
        if text:
            _download_weather_png()
            _save_weather_state(text, time.time() + WEATHER_FETCH_INTERVAL)
            return text
    except Exception as e:
        print(f"[kstardict] _fetch_weather_now error: {e}")
    old_text, _ = _load_weather_state()
    return old_text


def _fetch_weather_cached():
    """带缓存的天气获取：过期才请求 Web。"""
    if _should_fetch_weather():
        return _fetch_weather_now()
    text, _ = _load_weather_state()
    return text or "天气加载中…"


# ============================================================
# KRunner 输入框重置 / 关闭
# ============================================================


def _find_qdbus():
    for candidate in ("qdbus", "qdbus6"):
        try:
            subprocess.run([candidate, "--help"], capture_output=True, timeout=1)
            return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return "qdbus"


def _get_qdbus():
    global _QDBUS
    if _QDBUS is None:
        _QDBUS = _find_qdbus()
    return _QDBUS


def _query_tr():
    """通过 qdbus 将 KRunner 输入框重置为 'tr '。"""
    global _last_query
    _last_query = ""

    def _run():
        qdbus = _get_qdbus()
        try:
            subprocess.run(["krunner"], capture_output=True, timeout=2)
            time.sleep(0.2)
            subprocess.run(
                [qdbus, "org.kde.krunner", "/App", "display"],
                capture_output=True,
                timeout=2,
            )
            subprocess.run(
                [qdbus, "org.kde.krunner", "/App", "query", "tr "],
                capture_output=True,
                timeout=2,
            )
        except Exception as e:
            print(f"[kstardict] _query_tr error: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _destroy_krunner_window():
    def _run():
        qdbus = _get_qdbus()
        try:
            r = subprocess.run(
                [qdbus, "org.kde.krunner", "/App", "toggleDisplay"],
                capture_output=True,
                timeout=2,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass
        try:
            subprocess.run(["xdotool", "key", "Escape"], timeout=1)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _dismiss_krunner(word=""):
    global _bcloser
    if not word or len(word) < MIN_WORD_LEN:
        with _lock:
            _pending_word = ""
        _destroy_popup()
        _query_tr()
        return
    _bcloser = len(word) - 1
    _destroy_krunner_window()
    _destroy_popup()


def _reset_to_trigger(word_for_reset=""):
    global _pending_word, _last_query
    with _lock:
        _pending_word = ""
    _last_query = "tr "
    _popup_queue.put(None)
    _query_tr()
    _dismiss_krunner(word_for_reset)


# ============================================================
# 翻译结果 HTML → PNG（900px 宽，黑底白字）
# 覆写策略：渲染前先删除旧 {word}.png
# ============================================================


def _safe_path(dir_path, filename):
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", filename)
    return os.path.join(dir_path, safe)


def _html_to_png(html_text, png_path):
    """将 HTML 翻译结果渲染为 900px 宽 PNG，黑底白字。"""
    if not html_text:
        return False
    os.makedirs(os.path.dirname(png_path), exist_ok=True)

    # 提取纯文本
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html_mod.unescape(text)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return False

    try:
        from PIL import Image, ImageDraw, ImageFont

        width = 900
        line_height = 22
        padding = 16
        height = padding * 2 + line_height * len(lines)
        img = Image.new("RGB", (width, height), "#000000")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 13
            )
        except Exception:
            font = ImageFont.load_default()
        y = padding
        for line in lines:
            draw.text((padding, y), line, fill="#ffffff", font=font)
            y += line_height
        img.save(png_path, "PNG")
        return True
    except ImportError:
        with open(png_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return False


def _detect_cjk_font(size=13):
    """探测系统中可用的等宽/中文 TTF/OTC 字体，返回 ImageFont 对象。"""
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # 兜底：让 PIL 自己找默认字体
    try:
        return ImageFont.truetype("DejaVuSansMono.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _text_to_png(text_content, png_path, width=900):
    """将纯文本内容渲染为 PNG（黑底白字），用于帮助文档等。"""
    if not text_content:
        return False
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    lines = [l for l in text_content.splitlines() if l.strip()]
    if not lines:
        # 即使全空也生成一张空图
        lines = ["(空内容)"]

    try:
        from PIL import Image, ImageDraw, ImageFont
        
        line_height = 22
        padding = 16
        height = padding * 2 + line_height * len(lines)
        img = Image.new("RGB", (width, height), "#000000")
        draw = ImageDraw.Draw(img)
        font = _detect_cjk_font(13)
        y = padding
        for line in lines:
            draw.text((padding, y), line, fill="#ffffff", font=font)
            y += line_height
        img.save(png_path, "PNG")
        return True
    except ImportError:
        print(f"[kstardict] 未安装Pillow，无法生成PNG，已生成文本文件: {png_path}")
        with open(png_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return False
    except Exception as e:
        print(f"[kstardict] _text_to_png error: {e}")
        return False


def _ensure_translate_png(word, html_text):
    """确保 ~/.cache/kstardict/{word}.png 存在且为最新（先删旧再渲染覆写）。"""
    cache_dir = CACHE_KSTARDICT_DIR
    os.makedirs(cache_dir, exist_ok=True)
    png_path = _safe_path(cache_dir, f"{word}.png")
    try:
        if os.path.exists(png_path):
            os.remove(png_path)
    except Exception:
        pass
    if _html_to_png(html_text, png_path):
        return png_path
    return None


def _open_translate_image(word, html_text):
    """feh 打开翻译 PNG：800x500 + 同目录翻页。"""
    png_path = _ensure_translate_png(word, html_text)
    if not png_path or not os.path.exists(png_path):
        return
    try:
        subprocess.Popen(
            ["feh", "--geometry", "800x500", "--start-at", f"./{word}.png", "."],
            cwd=CACHE_KSTARDICT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        for alt in ("eog", "gwenview", "ristretto"):
            try:
                subprocess.Popen(
                    [alt, png_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                break
            except FileNotFoundError:
                continue


def _open_weather_image():
    """feh 打开 W.png（先确保图片存在，不存在则下载）。"""
    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    if not os.path.exists(WEATHER_CACHE_PNG):
        _download_weather_png()
    if not os.path.exists(WEATHER_CACHE_PNG):
        return
    try:
        subprocess.Popen(
            ["feh", "--geometry", "800x500", "--start-at", "./W.png", "."],
            cwd=WEATHER_CACHE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        for opener in ("xdg-open", "eog", "gwenview"):
            try:
                subprocess.Popen(
                    [opener, WEATHER_CACHE_PNG],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                break
            except FileNotFoundError:
                continue

def _generate_help_png():
    """根据模块 docstring 生成 HELP.png，存于 ~/.cache/kstardict/。"""
    cache_dir = CACHE_KSTARDICT_DIR
    os.makedirs(cache_dir, exist_ok=True)
    png_path = os.path.join(cache_dir, "HELP.png")
    try:
        if os.path.exists(png_path):
            os.remove(png_path)
            print(f"[kstardict] 已删除旧HELP.png: {png_path}")
    except Exception as e:
        print(f"[kstardict] _generate_help_png remove old error: {e}")

    # 使用模块顶部 docstring 作为帮助内容
    help_text = _HELP_DOC.strip() if _HELP_DOC.strip() else "kstardict 帮助文档"
    ok = _text_to_png(help_text, png_path)
    if ok and os.path.exists(png_path):
        print(f"[kstardict] HELP.png generated: {png_path}")
        return png_path
    print(f"[kstardict] HELP.png generation FAILED (Pillow unavailable or font error)")
    return None

-------------------------------------------------------------------------------------------------------------------
def _open_help_image():
    """生成并在 feh 中打开 HELP.png；feh 不可用时回退到其他图片查看器。"""
    print(f"[kstardict] 触发打开帮助文档...")
    png_path = _generate_help_png()
    if not png_path:
        print(f"[kstardict] 打开失败：HELP.png不存在")
        return
    viewers = ["feh", "eog", "gwenview", "ristretto", "xdg-open"]
    for viewer in viewers:
        try:
            if viewer == "feh":
                subprocess.Popen(
                    ["feh", "--geometry", "1200x750", "--start-at", "./HELP.png", "."],
                    cwd=CACHE_KSTARDICT_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[kstardict] feh已启动，打开:{png_path}")
            else:
                subprocess.Popen(
                    [viewer, png_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            print(f"[kstardict] HELP.png opened with: {viewer}")
            break
        except FileNotFoundError:
            continue
    print(f"[kstardict] no image viewer found; HELP.png at {png_path}")


def _open_kde_calendar():
    """
    打开 KDE 系统日历。
    优先通过 dbus 激活 KDE 时钟/日历 plasmoid 的日历视图；
    若 dbus 方式不可用，则回退到启动 KDE 日历应用（korganizer / kalendar）。
    """
    print(f"[kstardict] 触发打开 KDE 系统日历...")

    # 方式一：dbus 触发 KDE 时钟 applet 展开日历（org.kde.plasma.calendar）
    # 多数 KDE 桌面的时钟 plasmoid 提供 showCalendar()/toggleCalendar()
    try:
        bus = dbus.SessionBus()
        # 尝试 org.kde.plasmashell 的日历接口
        obj = bus.get_object("org.kde.plasmashell", "/PlasmaShell")
        iface = dbus.Interface(obj, "org.kde.PlasmaShell")
        # showCalendar 在大多数 KDE 版本可用
        try:
            iface.showCalendar()
            print(f"[kstardict] KDE 日历已通过 plasmashell.showCalendar() 打开")
            return
        except dbus.exceptions.DBusException:
            pass
    except Exception as e:
        print(f"[kstardict] plasmashell dbus 不可用: {e}")

    # 方式二：直接调用 KDE 日历应用
    for app in ("korganizer", "kalendar", "kcalendar"):
        try:
            subprocess.Popen(
                [app],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[kstardict] 已启动 KDE 日历应用: {app}")
            return
        except FileNotFoundError:
            continue

    print(f"[kstardict] 未找到可用的 KDE 日历应用（korganizer/kalendar）")


def _open_cambalache():
    """
    打开 /usr/bin/cambalache（GTK 界面设计器）。
    cambalache 不可用时回退到其他 GUI 设计工具（glade）便于排查。
    """
    print(f"[kstardict] 触发打开 cambalache...")
    cambalache_bin = "/usr/bin/cambalache"
    if os.path.exists(cambalache_bin):
        try:
            subprocess.Popen(
                [cambalache_bin],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[kstardict] cambalache 已启动: {cambalache_bin}")
            return
        except Exception as e:
            print(f"[kstardict] 启动 cambalache 失败: {e}")
    else:
        print(f"[kstardict] {cambalache_bin} 不存在，尝试 glade 作为回退...")
    # 回退：glade（GTK 旧版界面设计器）
    try:
        subprocess.Popen(
            ["glade"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[kstardict] 已启动回退应用: glade")
    except FileNotFoundError:
        print(f"[kstardict] cambalache 与 glade 均未安装")


# ============================================================
# HTML 清洗 + QStarDict 翻译
# ============================================================


def strip_html(html_text: str):
    if not html_text:
        return "", None
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html_mod.unescape(text)
    kept = [line for line in text.splitlines() if line.strip()]
    text = "\n".join(kept)
    text = re.sub(r"\n\*\[", "  *__BRACKET__", text)
    text = re.sub(r"(?<!\n)\[", "\n[", text)
    text = text.replace("__BRACKET__", "[")
    _half_to_full = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
    text = re.sub(
        r"(?<![a-zA-Z])[,\.;:!?()\[\]]",
        lambda m: _half_to_full.get(m.group(), m.group()),
        text,
    )
    lines = text.splitlines()
    dict_name = None
    if lines and re.match(r"^[\(（]", lines[-1]):
        lines = lines[:-1]
    if lines and re.match(r"^[^a-zA-Z]", lines[0]):
        dict_name = lines[0]
        lines = lines[1:]
    return "\n".join(lines), dict_name


def qstardict_translate(word: str, retries: int = 1) -> tuple:
    try:
        bus = dbus.SessionBus()
        proxy = bus.get_object(QSTAR, QOBJ)
        iface = dbus.Interface(proxy, QSTAR)
    except dbus.exceptions.DBusException:
        return "", "", None
    for attempt in range(retries + 1):
        try:
            html = iface.translateHtml(word)
        except dbus.exceptions.DBusException:
            html = ""
        main_text, dict_name = strip_html(html)
        if main_text:
            return html, main_text, dict_name
        if attempt < retries:
            time.sleep(0.2)
    return "", "", None


# ============================================================
# Tk 浮窗
# ============================================================


def _tk_exists(widget) -> bool:
    try:
        return widget is not None and widget.winfo_exists()
    except (tk.TclError, AttributeError):
        return False




def kstardict_close_popup():
    """
    统一关闭浮窗入口：供 DBus ClosePopup / 全局热键 / 浮窗内关闭按钮共用。
    浮窗本身无键盘焦点，故 <Escape> 绑定无效；改为由桌面环境全局快捷键
    调用 kstardict-close.sh -> dbus-send -> 本函数，完成浮窗关闭与清理。
    """
    global _active_popup
    if _active_popup is None:
        return
    popup = _active_popup
    try:
        popup.destroy()
    except tk.TclError:
        pass
    _active_popup = None
    _query_tr()
    _dismiss_krunner(_pending_word)


def _destroy_popup():
    global _active_popup
    with _popup_lock:
        popup = _active_popup
        _active_popup = None
    if popup and _tk_exists(popup):
        try:
            popup.destroy()
        except tk.TclError:
            pass


def _today_str():
    now = time.localtime()
    hour = now.tm_hour
    if hour == 0:
        h12, ampm = 12, "AM"
    elif hour == 12:
        h12, ampm = 12, "PM"
    elif hour < 12:
        h12, ampm = hour, "AM"
    else:
        h12, ampm = hour - 12, "PM"
    return f"{now.tm_year}-{now.tm_mon}-{now.tm_mday}-{h12}:{now.tm_min:02d} {ampm}"


# ---------- 内嵌说明浮窗（Tooltip）辅助 ----------
class _BarTooltip:
    """在状态栏元素旁弹出一个小型说明浮窗（Toplevel），靠近对应元素。"""

    def __init__(self, master_popup, text, anchor_widget):
        self._tip = None
        self._master = master_popup
        self._text = text
        self._anchor = anchor_widget

    def show(self):
        if self._tip and _tk_exists(self._tip):
            return
        tip = tk.Toplevel(self._master)
        tip.overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        tip.wm_attributes("-alpha", 0.95)
        tip.configure(bg="#2b2d35")
        lbl = tk.Label(
            tip,
            text=self._text,
            bg="#2b2d35",
            fg="#e8e8ea",
            font=(FONT_FAMILY, 9),
            padx=8,
            pady=4,
            anchor="w",
            justify="left",
        )
        lbl.pack(fill="both", expand=True)
        tip.update_idletasks()
        # 定位：在 anchor_widget 上方居中
        try:
            mx = self._anchor.winfo_rootx()
            my = self._anchor.winfo_rooty()
            aw = self._anchor.winfo_width()
            tw = tip.winfo_width()
            th = tip.winfo_height()
            x = mx + max(0, (aw - tw) // 2)
            y = my - th - 4
            if y < 0:
                y = my + self._anchor.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        self._tip = tip

    def hide(self):
        if self._tip and _tk_exists(self._tip):
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
        self._tip = None


def _tk_worker():
    global _tk_root, _active_popup
    _tk_root = tk.Tk()
    _tk_root.withdraw()

    def _process_queue():
        global _active_popup
        try:
            while True:
                payload = _popup_queue.get_nowait()

                if payload is None:
                    _destroy_popup()
                    continue

                # 天气更新回到 Tk 主线程
                if isinstance(payload, tuple) and payload[0] == "weather":
                    _, text, update_fn = payload
                    if callable(update_fn):
                        update_fn(text)
                    continue

                # 打开天气图片
                if isinstance(payload, tuple) and payload[0] == "open_weather_image":
                    threading.Thread(target=_open_weather_image, daemon=True).start()
                    continue

                # 打开翻译 PNG
                if isinstance(payload, tuple) and payload[0] == "open_translate_image":
                    _, word, html_text = payload
                    threading.Thread(
                        target=_open_translate_image,
                        args=(word, html_text),
                        daemon=True,
                    ).start()
                    continue

                # 打开帮助文档图片
                if isinstance(payload, tuple) and payload[0] == "open_help_image":
                    threading.Thread(target=_open_help_image, daemon=True).start()
                    continue

                # 打开 KDE 系统日历
                if isinstance(payload, tuple) and payload[0] == "open_kde_calendar":
                    threading.Thread(target=_open_kde_calendar, daemon=True).start()
                    continue

                # 打开 /usr/bin/cambalache
                if isinstance(payload, tuple) and payload[0] == "open_cambalache":
                    threading.Thread(target=_open_cambalache, daemon=True).start()
                    continue

                # DBus ClosePopup 请求：在主线程关闭浮窗
                if isinstance(payload, tuple) and payload[0] == "close_popup":
                    kstardict_close_popup()
                    continue

                _destroy_popup()

                if isinstance(payload, tuple):
                    html_text, main_text, dict_name = payload
                else:
                    html_text, main_text, dict_name = payload, None, None

                popup = tk.Toplevel(_tk_root)
                popup.overrideredirect(True)
                popup.wm_attributes("-topmost", True)
                popup.wm_attributes("-alpha", 0.96)

                parent = popup
                for i, (color, thickness) in enumerate(SHADOW_LEVELS):
                    if i == 0:
                        popup.configure(bg=color)
                    if thickness == 0:
                        break
                    frame = tk.Frame(parent, bg=color, bd=0, highlightthickness=0)
                    frame.pack(fill="both", expand=True, padx=thickness, pady=thickness)
                    parent = frame

                content = tk.Frame(
                    parent,
                    bg=BG_COLOR,
                    highlightbackground=BORDER_COLOR,
                    highlightcolor=BORDER_COLOR,
                    highlightthickness=BORDER_WIDTH,
                    bd=0,
                )
                content.pack(
                    fill="both", expand=True, padx=SHADOW_MARGIN, pady=SHADOW_MARGIN
                )

                content.grid_rowconfigure(0, weight=1, minsize=100)
                content.grid_rowconfigure(1, weight=0)
                content.grid_columnconfigure(0, weight=1)

                # ---- 翻译区 ----
                text_frame = tk.Frame(content, bg=BG_COLOR)
                text_frame.grid(row=0, column=0, sticky="nsew", padx=16, pady=(12, 6))
                text_frame.rowconfigure(0, weight=1)
                text_frame.columnconfigure(0, weight=1)

                label = tk.Text(
                    text_frame,
                    bg=BG_COLOR,
                    fg=FG_COLOR,
                    font=(FONT_FAMILY, FONT_SIZE),
                    wrap="word",
                    spacing2=LINE_SPACING,
                    bd=0,
                    highlightthickness=0,
                    insertbackground=BG_COLOR,
                    selectbackground=BG_COLOR,
                    cursor="arrow",
                    state="disabled",
                )
                label.grid(row=0, column=0, sticky="nsew")

                scrollbar = tk.Scrollbar(
                    text_frame,
                    orient="vertical",
                    command=label.yview,
                    width=SCROLLBAR_WIDTH,
                    bg=SCROLLBAR_BG,
                    troughcolor=SCROLLBAR_BG,
                    activebackground=SCROLLBAR_COLOR,
                    elementborderwidth=0,
                    borderwidth=0,
                    highlightthickness=0,
                )
                scrollbar.grid(row=0, column=1, sticky="ns")

                def _on_text_scroll(first, last):
                    scrollbar.set(first, last)

                label.configure(yscrollcommand=_on_text_scroll)

                label.tag_configure("title", foreground="#dd4144")
                label.tag_configure("defn", foreground="#303534")
                label.tag_configure("meta", foreground="#f75457")

                label.config(state="normal")
                label.delete("1.0", "end")
                lines = main_text.splitlines()
                first = True
                for ln in lines:
                    if not ln.strip():
                        continue
                    s = ln.strip()
                    if first:
                        color_key = "title"
                        first = False
                    elif s.startswith("[") and s.endswith("]"):
                        color_key = "meta"
                    else:
                        color_key = "defn"
                    label.insert(
                        "end",
                        ("\n" if label.index("end-1c") != "1.0" else "") + ln,
                        color_key,
                    )
                label.config(state="disabled")

                # ===== 底部状态栏（固定高 STATUSBAR_HEIGHT=38px）=====
                statusbar = tk.Frame(
                    content, bg=STATUSBAR_BG, height=STATUSBAR_HEIGHT, bd=0
                )
                statusbar.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 5))
                statusbar.grid_propagate(False)
                statusbar.lift()
                statusbar.grid_rowconfigure(0, weight=1)
                statusbar.grid_columnconfigure(0, weight=0)  # 字典名
                statusbar.grid_columnconfigure(1, weight=1)  # word
                statusbar.grid_columnconfigure(2, weight=0)  # 天气
                statusbar.grid_columnconfigure(3, weight=0)  # 时间
                statusbar.grid_columnconfigure(4, weight=0)  # 滚动指示器

                # 字典名方框（左对齐，固定高 BOX_FIXED_HEIGHT=34px）
                dict_box = tk.Frame(
                    statusbar,
                    bg=BOX_HIGHLIGHT_BG,
                    highlightbackground=BOX_HIGHLIGHT_COLOR,
                    highlightcolor=BOX_HIGHLIGHT_COLOR,
                    highlightthickness=BOX_HIGHLIGHT_THICKNESS,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    height=BOX_FIXED_HEIGHT,
                )
                dict_box.grid(row=0, column=0, sticky="nsw", padx=(2, 4), pady=2)
                dict_box.grid_propagate(False)
                dict_var = tk.StringVar(value=dict_name or "")
                dict_label = tk.Label(
                    dict_box,
                    textvariable=dict_var,
                    bg=STATUSBAR_BG,
                    fg=DICT_NAME_FG,
                    font=(FONT_FAMILY, DICT_NAME_SIZE),
                    anchor="w",
                    padx=6,
                )
                dict_label.pack(fill="both", expand=True)

                # word 方框（固定字样 "word"，点击打开翻译 PNG，固定高 34px）
                word_box = tk.Frame(
                    statusbar,
                    bg=BOX_HIGHLIGHT_BG,
                    highlightbackground=BOX_HIGHLIGHT_COLOR,
                    highlightcolor=BOX_HIGHLIGHT_COLOR,
                    highlightthickness=BOX_HIGHLIGHT_THICKNESS,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    height=BOX_FIXED_HEIGHT,
                )
                word_box.grid(row=0, column=1, sticky="nsw", padx=(0, 4), pady=2)
                word_box.grid_propagate(False)
                word_label = tk.Label(
                    word_box,
                    text="word",
                    bg=STATUSBAR_BG,
                    fg="#dd4144",
                    font=(FONT_FAMILY, 10, "bold"),
                    anchor="center",
                    padx=6,
                )
                word_label.pack(fill="both", expand=True)

                # 天气方框（点击打开 W.png，固定高 34px）
                weather_box = tk.Frame(
                    statusbar,
                    bg=BOX_HIGHLIGHT_BG,
                    highlightbackground=BOX_HIGHLIGHT_COLOR,
                    highlightcolor=BOX_HIGHLIGHT_COLOR,
                    highlightthickness=BOX_HIGHLIGHT_THICKNESS,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    height=BOX_FIXED_HEIGHT,
                )
                weather_box.grid(row=0, column=2, sticky="ns", padx=(0, 4), pady=2)
                weather_box.grid_propagate(False)
                weather_var = tk.StringVar(value="天气加载中…")
                weather_label = tk.Label(
                    weather_box,
                    textvariable=weather_var,
                    bg=STATUSBAR_BG,
                    fg="#a9c7c0",
                    font=(FONT_FAMILY, 9),
                    anchor="e",
                    padx=6,
                )
                weather_label.pack(fill="both", expand=True)

                # 时间方框（固定高 34px）
                time_box = tk.Frame(
                    statusbar,
                    bg=BOX_HIGHLIGHT_BG,
                    highlightbackground=BOX_HIGHLIGHT_COLOR,
                    highlightcolor=BOX_HIGHLIGHT_COLOR,
                    highlightthickness=BOX_HIGHLIGHT_THICKNESS,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    height=BOX_FIXED_HEIGHT,
                )
                time_box.grid(row=0, column=3, sticky="ns", padx=(0, 4), pady=2)
                time_box.grid_propagate(False)
                time_var = tk.StringVar(value=_today_str())
                time_label = tk.Label(
                    time_box,
                    textvariable=time_var,
                    bg=STATUSBAR_BG,
                    fg="#a9c7c0",
                    font=(FONT_FAMILY, 9),
                    anchor="e",
                    padx=6,
                )
                time_label.pack(fill="both", expand=True)

                # 滚动指示器方框（▽，固定高 34px）
                scroll_box = tk.Frame(
                    statusbar,
                    bg=BOX_HIGHLIGHT_BG,
                    highlightbackground=BOX_HIGHLIGHT_COLOR,
                    highlightcolor=BOX_HIGHLIGHT_COLOR,
                    highlightthickness=BOX_HIGHLIGHT_THICKNESS,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    height=BOX_FIXED_HEIGHT,
                )
                scroll_box.grid(row=0, column=4, sticky="ns", padx=(0, 6), pady=2)
                scroll_box.grid_propagate(False)
                scroll_label = tk.Label(
                    scroll_box,
                    text=SCROLL_INDICATOR_TEXT,
                    bg=STATUSBAR_BG,
                    fg="#fe0004",
                    font=(FONT_FAMILY, 9),
                    anchor="center",
                    padx=6,
                )
                scroll_label.pack(fill="both", expand=True)

                # ---- 说明浮窗（Tooltip）实例 ----
                word_tip = _BarTooltip(popup, "点击Open...", word_box)
                scroll_tip = _BarTooltip(popup, "可滚动/下拉翻译区内容", scroll_box)
                dict_tip = _BarTooltip(popup, "点击打开帮助文档", dict_box)

                # ---- 鼠标悬浮/离开绑定 ----
                def _bind_hover(widget, tip):
                    widget.bind("<Enter>", lambda e: tip.show())
                    widget.bind("<Leave>", lambda e: tip.hide())

                _bind_hover(word_box, word_tip)
                _bind_hover(word_label, word_tip)
                _bind_hover(scroll_box, scroll_tip)
                _bind_hover(scroll_label, scroll_tip)
                _bind_hover(dict_box, dict_tip)
                _bind_hover(dict_label, dict_tip)

                # 时间方框悬浮提示：大概时间
                time_tip = _BarTooltip(popup, "大概时间", time_box)
                _bind_hover(time_box, time_tip)
                _bind_hover(time_label, time_tip)

                # ---- 点击事件 ----
                def _on_word_click(_event=None):
                    _popup_queue.put(("open_translate_image", _pending_word, html_text))

                word_box.bind("<Button-1>", _on_word_click)
                word_label.bind("<Button-1>", _on_word_click)

                def _on_weather_click(_event=None):
                    _popup_queue.put(("open_weather_image",))

                weather_box.bind("<Button-1>", _on_weather_click)
                weather_label.bind("<Button-1>", _on_weather_click)

                def _on_scroll_click(_event=None):
                    _popup_queue.put(("open_cambalache",))

                scroll_box.bind("<Button-1>", _on_scroll_click)
                scroll_label.bind("<Button-1>", _on_scroll_click)

                def _on_dict_click(_event=None):
                    _popup_queue.put(("open_help_image",))

                dict_box.bind("<Button-1>", _on_dict_click)
                dict_label.bind("<Button-1>", _on_dict_click)

                # 时间方框：点击打开 KDE 系统日历
                def _on_time_click(_event=None):
                    _popup_queue.put(("open_kde_calendar",))

                time_box.bind("<Button-1>", _on_time_click)
                time_label.bind("<Button-1>", _on_time_click)

                # ---- 天气异步加载（带 30 分钟缓存）----
                def _update_weather(text):
                    try:
                        if _tk_exists(popup):
                            weather_var.set(text)
                    except Exception:
                        pass

                cached_text, _ = _load_weather_state()
                if cached_text:
                    weather_var.set(cached_text)

                def _fetch_and_update_weather():
                    text = _fetch_weather_cached()
                    _popup_queue.put(("weather", text, _update_weather))

                threading.Thread(target=_fetch_and_update_weather, daemon=True).start()

                # ---- 定位浮窗 ----
                tw, th = POPUP_WIDTH, POPUP_HEIGHT
                sw = popup.winfo_screenwidth()
                popup.geometry(f"{tw}x{th}+{(sw - tw) // 2}+56")
                popup.minsize(tw, th)
                popup.maxsize(tw, th)
                popup.resizable(False, False)
                popup.focus_force()

                if not _tk_exists(popup):
                    continue

                focus_widgets = [
                    popup,
                    content,
                    text_frame,
                    label,
                    scrollbar,
                    statusbar,
                    dict_box,
                    dict_label,
                    word_box,
                    word_label,
                    weather_box,
                    weather_label,
                    time_box,
                    time_label,
                    scroll_box,
                    scroll_label,
                ]

                def _make_close(p, fwidgets, word_for_close):
                    def _close(event=None):
                        global _active_popup, _bcloser
                        if p != _active_popup:
                            return
                        if event and getattr(event, "type", None) == 10:
                            try:
                                new_focus = p.focus_get()
                            except tk.TclError:
                                new_focus = None
                            if new_focus is not None and new_focus in fwidgets:
                                return
                        _bcloser = (len(word_for_close) - 1) if word_for_close else -1
                        try:
                            p.destroy()
                        except tk.TclError:
                            pass
                        _active_popup = None
                        _dismiss_krunner(word_for_close)

                    return _close

                close_fn = _make_close(popup, focus_widgets, _pending_word)
                popup.bind("<Escape>", close_fn)
                popup.bind("<FocusOut>", close_fn)

                def _on_mousewheel(event):
                    if not scrollbar.winfo_viewable():
                        return
                    if event.num == 4:
                        label.yview_scroll(-30, "pixels")
                    elif event.num == 5:
                        label.yview_scroll(30, "pixels")
                    return "break"

                for widget in (popup, content, text_frame, label):
                    widget.bind("<Button-4>", _on_mousewheel)
                    widget.bind("<Button-5>", _on_mousewheel)

                _active_popup = popup
        except queue.Empty:
            pass
        _tk_root.after(50, _process_queue)

    _tk_root.after(0, _process_queue)
    _tk_root.mainloop()


def show_floating_popup(payload):
    _popup_queue.put(payload)


# ============================================================
# 辅助：把翻译结果整理为 KRunner 可显示的标题+副文本
# ============================================================


def _build_krunner_result(word, main_text, dict_name):
    """
    把 main_text 拆成：
      - title：首行释义（截断到 80 字符）
      - subtext：剩余行拼接 + 词典名（截断到 200 字符）
    返回一条 KRunner 结果元组 (id, title, icon, type, relevance, props)
    """
    lines = [l.strip() for l in main_text.splitlines() if l.strip()]
    if not lines:
        lines = [word]

    # 标题：首行，截断防 UI 异常
    title_line = lines[0]
    if len(title_line) > 80:
        title_line = title_line[:77] + "..."

    # 副文本：剩余行
    rest_lines = lines[1:]
    if rest_lines:
        rest_text = " ".join(rest_lines)
        if len(rest_text) > 200:
            rest_text = rest_text[:197] + "..."
    else:
        rest_text = "翻译结果"

    if dict_name:
        subtext = f"[{dict_name}] {rest_text}"
    else:
        subtext = rest_text

    return (
        f"stardict-{word}",
        title_line,
        "accessories-dictionary",
        100,
        1.0,
        {"subtext": subtext},
    )


# ============================================================
# DBus 服务（KRunner 插件接口）
# ============================================================




# ============================================================
# DBus 服务：org.kstardict.popup  （全局关闭浮窗）
# 桌面环境把全局快捷键（如 Ctrl+Shift+Q）绑定到 kstardict-close.sh，
# 脚本通过 dbus-send 调用本服务的 ClosePopup 方法关闭浮窗。
# ============================================================

class KStarDictPopupService(dbus.service.Object):
    """浮窗控制 DBus 服务（session bus）。"""

    BUS_NAME = "org.kstardict.popup"
    OBJ_PATH = "/org/kstardict/popup"

    def __init__(self):
        bus_name = dbus.service.BusName(self.BUS_NAME, bus=dbus.SessionBus())
        super().__init__(bus_name, self.OBJ_PATH)
        print(f"[kstardict] DBus 服务已注册: {self.BUS_NAME}{self.OBJ_PATH}")

    @dbus.service.method("org.kstardict.popup", in_signature="", out_signature="b")
    def ClosePopup(self):
        """关闭当前活动浮窗并执行清理（KRunner 复位等）。"""
        print("[kstardict] DBus ClosePopup 被调用")
        # 必须在 Tk 主线程操作 widget，投到队列由 _tk_worker 处理
        _popup_queue.put(("close_popup",))
        return True

    @dbus.service.method("org.kstardict.popup", in_signature="", out_signature="b")
    def Ping(self):
        """连通性探测，便于脚本/快捷键判断服务是否在线。"""
        return True


class StarDictRunner(dbus.service.Object):
    def __init__(self):
        bus = dbus.SessionBus()
        bus.request_name(SERVICE)
        super().__init__(bus, PATH)
        threading.Thread(target=_tk_worker, daemon=True).start()

    @dbus.service.method(
        "org.kde.krunner1", in_signature="s", out_signature="a(sssida{sv})"
    )
    def Match(self, query):
        global _pending_word, _last_query

        q = (query or "").strip()

        # 特殊命令：tr ? / tr ?.  → 生成并在 feh 中打开 HELP.png
        if q in (TRIGGER + " ?", TRIGGER + " ?."):
            threading.Thread(target=_open_help_image, daemon=True).start()
            return [
                (
                    "stardict-help",
                    "帮助文档 HELP.png 已生成并在 feh 中打开",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": f"路径: {CACHE_KSTARDICT_DIR}/HELP.png"},
                )
            ]

        # BackSpace / 删除：重置
        if _last_query and len(q) < len(_last_query):
            _reset_to_trigger("")
            return [
                (
                    "stardict-hint",
                    "单词长度需 ≥ 2",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": ":tr hello."},
                )
            ]

        if _last_query == "":
            _last_query = q

        if q == TRIGGER or q == TRIGGER + " ":
            return [
                (
                    "stardict-hint",
                    "输入 tr wOrd. 进行翻译",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": "：tr hello."},
                )
            ]

        m = re.match(rf"^{re.escape(TRIGGER)}\s*(.+)\.\s*$", q)
        if not m:
            if q.startswith(TRIGGER):
                return [
                    (
                        "stardict-hint",
                        "输入[tr wOrd.]进行翻译",
                        "accessories-dictionary",
                        100,
                        1.0,
                        {"subtext": ":tr hello."},
                    )
                ]
            with _lock:
                _pending_word = ""
            return []

        word = m.group(1).rstrip(".")
        if not word or len(word) < MIN_WORD_LEN:
            with _lock:
                _pending_word = ""
            return [
                (
                    "stardict-hint",
                    f"单词长度需 ≥ {MIN_WORD_LEN}",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": ":tr hello."},
                )
            ]

        with _lock:
            _pending_word = word

        html_text, main_text, dict_name = qstardict_translate(word)
        if not main_text:
            return [
                (
                    f"stardict-{word}",
                    f"{word} 未收录",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": "无该单词的翻译"},
                )
            ]

        # 后台生成翻译 PNG（先删旧再覆写，不阻塞 KRunner 返回）
        threading.Thread(
            target=_ensure_translate_png, args=(word, html_text), daemon=True
        ).start()

        # ★ 弹 Tk 浮窗（含完整译文 + 状态栏）
        show_floating_popup((html_text, main_text, dict_name))

        # ★ 同时在 KRunner 结果列表中显示翻译内容
        return [_build_krunner_result(word, main_text, dict_name)]

    @dbus.service.method("org.kde.krunner1", in_signature="", out_signature="")
    def Teardown(self):
        _reset_to_trigger(_pending_word)

    @dbus.service.method("org.kde.krunner1", in_signature="ss", out_signature="")
    def Run(self, id, action_id):
        """
        点击 KRunner 结果项时的动作。
        需求：不弹窗，仅关闭 KRunner 即可（弹窗已在 Match 阶段触发）。
        """
        _dismiss_krunner(_pending_word)


if __name__ == "__main__":
    DBusGMainLoop(set_as_default=True)
    # 先启动浮窗控制 DBus 服务（全局关闭浮窗）
    KStarDictPopupService()
    # 再启动 KRunner 插件服务
    StarDictRunner()
    GLib.MainLoop().run()
