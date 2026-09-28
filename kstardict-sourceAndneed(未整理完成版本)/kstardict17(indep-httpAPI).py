#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kstardict16.py —— 在 v15 基础上新增「http API 活动浮窗层」

相对 v15 的新增/变更（其余逻辑完全保留）：
  ★ 新增常量区 HTTP_* / LAYER_*：http API 配置 + 活动浮窗尺寸/颜色。
  ★ 新增 _HttpApiLayer 类：独立的 http 数据层。
      - 完全独立线程拉取，绝不阻塞字典查询主流程；
      - 数据只渲染到自己这一层（WebView/文字 Toplevel），不混入翻译区；
      - 默认收缩为「状态栏上方、右上角」的 9x9px 指示器（上浮 ▲）；
      - 点击指示器展开 ↔ 收起，展开时指示器变为「下沉」▼（同为 9x9px）；
      - z-order：翻译区(text_frame) < 本层 < 状态栏(statusbar)。
  ★ _tk_worker 构建 popup 时：注入 _HttpApiLayer，并把三层 pack 顺序调整为
      text_frame  →  http_layer  →  statusbar（保证层叠关系）。
  ★ Match() 中拿到 word 后，仅"通知" http 层去异步刷新，不等它返回。

依赖：在 v15 基础上，可选 python requests；未安装时自动退化为 urllib。
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

# ============================================================
# ★ 新增：http API 活动浮窗层配置
# ============================================================
# 是否启用 http API 层（设为 False 即完全关闭，不发起任何网络请求）
HTTP_ENABLED = True

# http API 端点列表：可配置多个，依次尝试，取第一个成功返回。
# 每条 {searchWord} 占位符在请求时替换为当前查询词。
# 示例：
#   "https://api.example.com/lookup?word={searchWord}&key=APIKEY"
# 如需 HOSTKEY/签名，自行拼接到 URL 模板即可（本层只做占位替换 + 透传）。
HTTP_API_ENDPOINTS = [
    # "https://api.dictionaryapi.dev/api/v2/entries/en/{searchWord}",
]

# 请求超时（秒）。保持短超时，避免拖慢 UI；失败由独立线程静默吞掉。
HTTP_TIMEOUT = 6

# 同一单词两次请求的最小间隔（秒），防止滚动/连查时疯狂打网络
HTTP_MIN_INTERVAL = 1.5

# ---- 活动浮窗（http 层）视觉配置 ----
LAYER_BG = "#1e2030"           # 展开区背景（深色，与翻译区区分）
LAYER_FG = "#e8e8ea"           # 展开区文字
LAYER_BORDER = "#3aa0ff"       # 边框高亮色（上浮/下沉指示器同色）
LAYER_WIDTH = 420               # 展开区宽度
LAYER_MAX_HEIGHT = 260          # 展开区最大高度（超出滚动）
INDICATOR_SIZE = 9              # ★ 9x9 px 指示器（上浮/下沉共用尺寸）
INDICATOR_UP = "\u25b2"         # ▲ 上浮指示器（收起态，点击展开）
INDICATOR_DOWN = "\u25bc"       # ▼ 下沉指示器（展开态，点击收起）

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
    from PIL import ImageFont

    return ImageFont.load_default()


# ============================================================
# 天气缓存：~/.cache/weatherWTTR/W_state.json
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
# 翻译结果 HTML → PNG
# ============================================================


def _safe_path(dir_path, filename):
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", filename)
    return os.path.join(dir_path, safe)


def _html_to_png(html_text, png_path):
    if not html_text:
        return False
    os.makedirs(os.path.dirname(png_path), exist_ok=True)

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
    try:
        return ImageFont.truetype("DejaVuSansMono.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _text_to_png(text_content, png_path, width=900):
    if not text_content:
        return False
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    lines = [l for l in text_content.splitlines() if l.strip()]
    if not lines:
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
    cache_dir = CACHE_KSTARDICT_DIR
    os.makedirs(cache_dir, exist_ok=True)
    png_path = os.path.join(cache_dir, "HELP.png")
    try:
        if os.path.exists(png_path):
            os.remove(png_path)
            print(f"[kstardict] 已删除旧HELP.png: {png_path}")
    except Exception as e:
        print(f"[kstardict] _generate_help_png remove old error: {e}")

    help_text = _HELP_DOC.strip() if _HELP_DOC.strip() else "kstardict 帮助文档"
    ok = _text_to_png(help_text, png_path)
    if ok and os.path.exists(png_path):
        print(f"[kstardict] HELP.png generated: {png_path}")
        return png_path
    print(f"[kstardict] HELP.png generation FAILED (Pillow unavailable or font error)")
    return None


def _open_help_image():
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
    print(f"[kstardict] 触发打开 KDE 系统日历...")
    try:
        bus = dbus.SessionBus()
        obj = bus.get_object("org.kde.plasmashell", "/PlasmaShell")
        iface = dbus.Interface(obj, "org.kde.PlasmaShell")
        try:
            iface.showCalendar()
            print(f"[kstardict] KDE 日历已通过 plasmashell.showCalendar() 打开")
            return
        except dbus.exceptions.DBusException:
            pass
    except Exception as e:
        print(f"[kstardict] plasmashell dbus 不可用: {e}")

    for app in ("korganizer", "kalendar", "kcalendar"):
        try:
            subprocess.Popen([app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[kstardict] 已启动 KDE 日历应用: {app}")
            return
        except FileNotFoundError:
            continue

    print(f"[kstardict] 未找到可用的 KDE 日历应用（korganizer/kalendar）")


def _open_cambalache():
    print(f"[kstardict] 触发打开 cambalache...")
    cambalache_bin = "/usr/bin/cambalache"
    if os.path.exists(cambalache_bin):
        try:
            subprocess.Popen(
                [cambalache_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print(f"[kstardict] cambalache 已启动: {cambalache_bin}")
            return
        except Exception as e:
            print(f"[kstardict] 启动 cambalache 失败: {e}")
    else:
        print(f"[kstardict] {cambalache_bin} 不存在，尝试 glade 作为回退...")
    try:
        subprocess.Popen(["glade"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
# ★ 新增：http API 数据层（独立线程，不阻塞字典查询）
# ============================================================


def _http_fetch(word: str) -> str:
    """
    在独立线程中请求 HTTP_API_ENDPOINTS，返回渲染好的纯文本。
    失败/禁用/无端点时返回空串——调用方（UI）静默忽略，绝不抛异常。
    此函数可被 requests / urllib 任一可用后端驱动。
    """
    if not (HTTP_ENABLED and word and HTTP_API_ENDPOINTS):
        return ""

    # 惰性导入：优先 requests，回退 urllib
    last_err = ""
    for raw_url in HTTP_API_ENDPOINTS:
        url = raw_url.replace("{searchWord}", urllib.request.quote(word, safe=""))
        try:
            try:
                import requests

                r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "kstardict/16"})
                r.raise_for_status()
                payload = r.text
            except ImportError:
                req = urllib.request.Request(url, headers={"User-Agent": "kstardict/16"})
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    payload = resp.read().decode("utf-8", errors="replace")

            # 若是 JSON，做一个简洁的扁平化，避免把原始 JSON 直接丢给用户
            try:
                data = json.loads(payload)
                return _http_flatten(data, word)
            except (json.JSONDecodeError, ValueError):
                # 已是 HTML/纯文本，直接返回（去掉标签）
                txt = re.sub(r"<br\s*/?>", "\n", payload, flags=re.IGNORECASE)
                txt = re.sub(r"<[^>]+>", "", txt)
                return _html_mod.unescape(txt).strip()
        except Exception as e:
            last_err = str(e)
            continue
    print(f"[kstardict] http API 全部端点失败({word}): {last_err}")
    return ""


def _http_flatten(data, word: str, indent: int = 0) -> str:
    """把常见字典 API 的 JSON 结构扁平化成多行文本。"""
    pad = "  " * indent
    lines = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_http_flatten(v, word, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(_http_flatten(item, word, indent + 1))
            else:
                lines.append(f"{pad}- {item}")
    else:
        return f"{pad}{data}"
    return "\n".join(lines)


class _HttpApiLayer:
    """
    http API 活动浮窗层。

    布局（均在 popup 内部，随 popup 一起移动/销毁）：
        text_frame (字典查询结果)        ← 最底层
        [本层]  _shell / _body          ← 中间层（位于翻译区之上）
        statusbar (状态栏)              ← 最顶层

    视觉状态：
        收起：仅右上角 9x9px 「▲ 上浮」指示器（位于 statusbar 下方紧贴其上）。
        展开：指示器变 「▼ 下沉」，下方展开 LAYER_MAX_HEIGHT 的内容区。

    线程模型：
        refresh(word) 只负责「去重 + 起线程」，立即返回；
        网络结果通过 popup.after(0, ...) 回到 Tk 主线程再渲染，
        因此永远不阻塞字典查询主流程。
    """

    def __init__(self, popup, content, statusbar):
        self._popup = popup        # 外层 Toplevel（用于 .after）
        self._content = content    # 浮窗内容 Frame（grid 父容器）
        self._statusbar = statusbar  # 状态栏 Frame（用于定位"上方"）

        self._expanded = False
        self._last_word = ""
        self._last_fetch_at = 0.0
        self._pending_word = ""
        self._fetch_lock = threading.Lock()
        self._in_flight = False

        # ---- 外壳：停靠在翻译区下方、状态栏上方 ----
        self._shell = tk.Frame(self._content, bg=LAYER_BG, bd=0, highlightthickness=0)
        # 见 _repack：由 popup 构建完毕后调用 repack() 插入到正确层叠位置

        # ---- 9x9 指示器（默认收起态 = 上浮 ▲）----
        self._indicator = tk.Label(
            self._shell,
            text=INDICATOR_UP,
            bg=LAYER_BG,
            fg=LAYER_BORDER,
            font=("TkFixedFont", 7),   # 9x9px 用极小字号
            width=1, height=1,
            cursor="hand2",
            padx=0, pady=0,
            relief="solid",
            bd=1,
            highlightbackground=LAYER_BORDER,
            highlightcolor=LAYER_BORDER,
            highlightthickness=1,
        )
        self._indicator.pack(side="top", anchor="ne", padx=4, pady=(0, 2))
        self._indicator.bind("<Button-1>", self._toggle)
        self._indicator_tooltip = _BarTooltip(
            self._popup, "点击展开/收起在线释义（http API，独立加载不阻塞）", self._indicator
        )
        self._indicator.bind("<Enter>", lambda e: self._indicator_tooltip.show())
        self._indicator.bind("<Leave>", lambda e: self._indicator_tooltip.hide())

        # ---- 展开内容区 ----
        self._body = tk.Frame(self._shell, bg=LAYER_BG, bd=0, highlightthickness=0)
        self._body_scrollbar = tk.Scrollbar(self._body, orient="vertical")
        self._body_text = tk.Text(
            self._body,
            bg=LAYER_BG,
            fg=LAYER_FG,
            font=(FONT_FAMILY, 10),
            wrap="word",
            relief="flat",
            bd=0,
            padx=8, pady=6,
            yscrollcommand=self._body_scrollbar.set,
            height=10,
            width=LAYER_WIDTH // 8,
            cursor="arrow",
        )
        self._body_scrollbar.config(command=self._body_text.yview)
        self._body_scrollbar.pack(side="right", fill="y")
        self._body_text.pack(side="left", fill="both", expand=True)
        self._body_text.configure(state="disabled")
        self._body.pack_forget()  # 收起态隐藏

        self._render_placeholder()

    # ---------- 层叠顺序：翻译区 < 本层 < statusbar ----------
    def repack(self):
        """
        由 popup 构建收尾时调用。
        将本层插入到 content 的 grid 布局中：row=1（翻译区 row=0，状态栏 row=2）。
        注：原 statusbar 占 row=1，此处统一重排为 翻译(0) → http层(1) → statusbar(2)。
        """
        # 先让 statusbar 让出 row=1
        self._statusbar.grid_forget()
        self._shell.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 2))
        self._statusbar.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 5))
        # 保证本层在翻译区之上、在 statusbar 之下
        self._shell.lift(self._content.winfo_children()[0])  # 抬到翻译区之上
        self._statusbar.lift(self._shell)                    # 状态栏再抬到本层之上

    # ---------- 对外接口：仅触发异步刷新，绝不阻塞 ----------
    def refresh(self, word: str):
        """由 Match() 在主线程调用。仅去重 + 起后台线程，立即返回。"""
        word = (word or "").strip()
        if not word:
            return
        now = time.time()
        if word == self._last_word and (now - self._last_fetch_at) < HTTP_MIN_INTERVAL:
            # 同一词且未过期，直接复用已渲染内容（若已有则展开保持不变）
            return
        self._pending_word = word
        if self._in_flight:
            # 已有请求在飞，让旧线程跑完即可，不堆叠
            return
        self._in_flight = True
        self._last_word = word
        self._last_fetch_at = now
        threading.Thread(target=self._fetch_worker, args=(word,), daemon=True).start()

    # ---------- 内部：后台线程 ----------
    def _fetch_worker(self, word: str):
        try:
            result = _http_fetch(word)
        finally:
            self._in_flight = False
        # ★ 结果回 Tk 主线程渲染，避免跨线程操作 widget
        try:
            self._popup.after(0, self._apply_result, result)
        except tk.TclError:
            pass

    def _apply_result(self, text: str):
        self._set_body(text)
        if text:
            # 有数据则自动展开，方便用户即时看到；用户可再次点击收起
            self.expand()
        # 无数据（端点未配/失败）保持收起，不干扰界面

    # ---------- 指示器：上浮/下沉切换 ----------
    def _toggle(self, _event=None):
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._indicator.config(text=INDICATOR_DOWN)  # ▼ 下沉
        self._body.pack(side="top", fill="both", expand=True, padx=2, pady=(0, 4))
        self._shell.configure(highlightbackground=LAYER_BORDER, highlightthickness=1)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._indicator.config(text=INDICATOR_UP)  # ▲ 上浮
        self._body.pack_forget()
        self._shell.configure(highlightbackground=LAYER_BG, highlightthickness=0)

    # ---------- 内容渲染 ----------
    def _set_body(self, text: str):
        self._body_text.configure(state="normal")
        self._body_text.delete("1.0", "end")
        if text:
            self._body_text.insert("1.0", text.strip())
        else:
            self._body_text.insert("1.0", "（暂无 http API 数据：\n 未配置 HTTP_API_ENDPOINTS）")
        self._body_text.configure(state="disabled")

    def _render_placeholder(self):
        self._set_body("")


# ============================================================
# Tk 浮窗
# ============================================================


def _tk_exists(widget) -> bool:
    try:
        return widget is not None and widget.winfo_exists()
    except (tk.TclError, AttributeError):
        return False


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


# 每个 popup 对应的 http 层实例（popup 销毁时由 GC 回收）
_http_layer = None


def _tk_worker():
    global _tk_root, _active_popup, _http_layer
    _tk_root = tk.Tk()
    _tk_root.withdraw()

    def _process_queue():
        global _active_popup, _http_layer
        try:
            while True:
                payload = _popup_queue.get_nowait()

                if payload is None:
                    _destroy_popup()
                    _http_layer = None
                    continue

                # 天气更新回到 Tk 主线程
                if isinstance(payload, tuple) and payload[0] == "weather":
                    _, text, update_fn = payload
                    if callable(update_fn):
                        update_fn(text)
                    continue

                if isinstance(payload, tuple) and payload[0] == "open_weather_image":
                    threading.Thread(target=_open_weather_image, daemon=True).start()
                    continue

                if isinstance(payload, tuple) and payload[0] == "open_translate_image":
                    _, word, html_text = payload
                    threading.Thread(
                        target=_open_translate_image,
                        args=(word, html_text),
                        daemon=True,
                    ).start()
                    continue

                if isinstance(payload, tuple) and payload[0] == "open_help_image":
                    threading.Thread(target=_open_help_image, daemon=True).start()
                    continue

                if isinstance(payload, tuple) and payload[0] == "open_kde_calendar":
                    threading.Thread(target=_open_kde_calendar, daemon=True).start()
                    continue

                if isinstance(payload, tuple) and payload[0] == "open_cambalache":
                    threading.Thread(target=_open_cambalache, daemon=True).start()
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

                # ★ 关键：三层布局，为 http 层预留 row=1
                #   row 0: 翻译区(text_frame)
                #   row 1: http API 活动浮窗层（_HttpApiLayer，由 layer.repack() 接管）
                #   row 2: 状态栏(statusbar)
                content.grid_rowconfigure(0, weight=1, minsize=100)
                content.grid_rowconfigure(1, weight=0)   # http 层（固定高，收起时仅 9px）
                content.grid_rowconfigure(2, weight=0)   # 状态栏
                content.grid_columnconfigure(0, weight=1)

                # ---- 翻译区 ----
                text_frame = tk.Frame(content, bg=BG_COLOR)
                text_frame.grid(row=0, column=0, sticky="nsew")

                label = tk.Text(
                    text_frame,
                    bg=BG_COLOR,
                    fg=FG_COLOR,
                    font=(FONT_FAMILY, FONT_SIZE),
                    wrap="word",
                    relief="flat",
                    bd=0,
                    padx=10,
                    pady=8,
                    height=8,
                    cursor="arrow",
                    spacing1=LINE_SPACING,
                    spacing3=LINE_SPACING,
                )
                scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=label.yview)
                label.configure(yscrollcommand=scrollbar.set)
                scrollbar.pack(side="right", fill="y")
                label.pack(side="left", fill="both", expand=True)

                if main_text:
                    label.insert("1.0", main_text)
                label.configure(state="disabled")

                def _on_text_scroll(first, last):
                    label.yview_moveto(float(first))
                    return "break"

                label.configure(yscrollcommand=_on_text_scroll)

                # ===== 底部状态栏（固定高 STATUSBAR_HEIGHT=38px）=====
                statusbar = tk.Frame(
                    content, bg=STATUSBAR_BG, height=STATUSBAR_HEIGHT, bd=0
                )
                statusbar.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 5))
                statusbar.grid_propagate(False)
                statusbar.lift()
                statusbar.grid_rowconfigure(0, weight=1)
                statusbar.grid_columnconfigure(0, weight=0)  # 字典名
                statusbar.grid_columnconfigure(1, weight=1)  # word
                statusbar.grid_columnconfigure(2, weight=0)  # 天气
                statusbar.grid_columnconfigure(3, weight=0)  # 时间
                statusbar.grid_columnconfigure(4, weight=0)  # 滚动指示器

                # ---- ★ 构建 http API 活动浮窗层（插入 row=1，位于翻译区与状态栏之间）----
                layer = _HttpApiLayer(popup, content, statusbar)
                layer.repack()  # 重排为 翻译(0) → http层(1) → statusbar(2)
                _http_layer = layer

                # ---- 状态栏子元素（保持 v15 逻辑）----
                # 字典名
                dict_name_text = dict_name or "dict"
                dict_label = tk.Label(
                    statusbar,
                    text=dict_name_text,
                    bg=STATUSBAR_BG,
                    fg=DICT_NAME_FG,
                    font=(FONT_FAMILY, DICT_NAME_SIZE),
                    anchor="w",
                    padx=6,
                )
                dict_label.grid(row=0, column=0, sticky="ns", padx=(4, 2))

                # word
                word_label = tk.Label(
                    statusbar,
                    text=_pending_word or word,
                    bg=STATUSBAR_BG,
                    fg="#e8e8ea",
                    font=(FONT_FAMILY, 11),
                    anchor="w",
                    padx=6,
                )
                word_label.grid(row=0, column=1, sticky="ns")

                # 天气
                weather_label = tk.Label(
                    statusbar,
                    text=_fetch_weather_cached(),
                    bg=STATUSBAR_BG,
                    fg="#e8e8ea",
                    font=(FONT_FAMILY, 9),
                    anchor="e",
                    padx=6,
                )
                weather_label.grid(row=0, column=2, sticky="ns", padx=(0, 4))

                # 时间
                time_label = tk.Label(
                    statusbar,
                    text=_today_str(),
                    bg=STATUSBAR_BG,
                    fg="#e8e8ea",
                    font=(FONT_FAMILY, 9),
                    anchor="e",
                    padx=6,
                )
                time_label.grid(row=0, column=3, sticky="ns", padx=(0, 4))

                # 滚动指示器
                scroll_indicator = tk.Label(
                    statusbar,
                    text=SCROLL_INDICATOR_TEXT,
                    bg=STATUSBAR_BG,
                    fg="#e8e8ea",
                    font=(FONT_FAMILY, 9),
                    anchor="e",
                    padx=6,
                )
                scroll_indicator.grid(row=0, column=4, sticky="ns", padx=(0, 4))

                # hover 高亮
                def _bind_hover(widget, tip):
                    widget.bind("<Enter>", lambda e: (tip.show(), widget.configure(bg=BOX_HIGHLIGHT_COLOR)))
                    widget.bind("<Leave>", lambda e: (tip.hide(), widget.configure(bg=STATUSBAR_BG)))

                _bind_hover(dict_label, _BarTooltip(popup, "点击打开帮助文档", dict_label))
                _bind_hover(word_label, _BarTooltip(popup, "点击Open翻译图", word_label))
                _bind_hover(weather_label, _BarTooltip(popup, "点击Open天气图", weather_label))
                _bind_hover(time_label, _BarTooltip(popup, "点击打开日历", time_label))
                _bind_hover(scroll_indicator, _BarTooltip(popup, "滚轮可下拉内容...", scroll_indicator))

                # 点击行为
                def _on_word_click(_event=None):
                    _popup_queue.put(("open_translate_image", _pending_word, html_text))

                def _on_weather_click(_event=None):
                    _popup_queue.put(("open_weather_image",))

                def _on_scroll_click(_event=None):
                    _popup_queue.put(("open_cambalache",))

                def _on_dict_click(_event=None):
                    _popup_queue.put(("open_help_image",))

                def _on_time_click(_event=None):
                    _popup_queue.put(("open_kde_calendar",))

                word_label.bind("<Button-1>", _on_word_click)
                weather_label.bind("<Button-1>", _on_weather_click)
                scroll_indicator.bind("<Button-1>", _on_scroll_click)
                dict_label.bind("<Button-1>", _on_dict_click)
                time_label.bind("<Button-1>", _on_time_click)

                # 天气定时刷新
                def _update_weather(text):
                    try:
                        weather_label.configure(text=text)
                    except tk.TclError:
                        pass

                def _fetch_and_update_weather():
                    text = _fetch_weather_now()
                    _popup_queue.put(("weather", text, _update_weather))

                threading.Thread(target=_fetch_and_update_weather, daemon=True).start()

                # 每秒刷新时间
                def _tick():
                    try:
                        if _tk_exists(time_label):
                            time_label.configure(text=_today_str())
                    except tk.TclError:
                        pass
                    _tk_root.after(1000, _tick)

                _tick()

                # 关闭按钮
                def _make_close(p, fwidgets, word_for_close):
                    def _close(event=None):
                        global _active_popup, _bcloser
                        if p != _active_popup:
                            return
                        if event is not None and _bcloser > 0:
                            _bcloser -= 1
                            return
                        _active_popup = None
                        try:
                            p.destroy()
                        except tk.TclError:
                            pass
                        _reset_to_trigger(word_for_close)

                    for w in fwidgets:
                        w.bind("<Button-1>", _close)
                        w.bind("<Button-3>", _close)

                _make_close(popup, [popup, content, text_frame, label], _pending_word)

                # 滚轮
                def _on_mousewheel(event):
                    if not _tk_exists(label):
                        return
                    if event.num == 4:
                        label.yview_scroll(-30, "pixels")
                    elif event.num == 5:
                        label.yview_scroll(30, "pixels")
                    return "break"

                for widget in (popup, content, text_frame, label):
                    widget.bind("<Button-4>", _on_mousewheel)
                    widget.bind("<Button-5>", _on_mousewheel)

                # ★ 通知 http 层异步刷新（不阻塞，网络慢也不会卡翻译区）
                if _http_layer is not None:
                    _http_layer.refresh(_pending_word or word)

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
    lines = [l.strip() for l in main_text.splitlines() if l.strip()]
    if not lines:
        lines = [word]

    title_line = lines[0]
    if len(title_line) > 80:
        title_line = title_line[:77] + "..."

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
                    {"subtext": ":tr hello."},
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

        # 后台生成翻译 PNG
        threading.Thread(
            target=_ensure_translate_png, args=(word, html_text), daemon=True
        ).start()

        # ★ 弹 Tk 浮窗（含完整译文 + 状态栏 + http API 活动层）
        show_floating_popup((html_text, main_text, dict_name))

        # ★ 触发 http API 层异步刷新（在 _tk_worker 内真正执行，此处仅入队通知）
        # 注：实际刷新由 popup 构建时根据 _pending_word 自动触发，
        #     若需在数据到达前就占位，可在此扩展 _popup_queue 消息。

        return [_build_krunner_result(word, main_text, dict_name)]

    @dbus.service.method("org.kde.krunner1", in_signature="", out_signature="")
    def Teardown(self):
        _reset_to_trigger(_pending_word)

    @dbus.service.method("org.kde.krunner1", in_signature="ss", out_signature="")
    def Run(self, id, action_id):
        _dismiss_krunner(_pending_word)


if __name__ == "__main__":
    DBusGMainLoop(set_as_default=True)
    StarDictRunner()
    GLib.MainLoop().run()
