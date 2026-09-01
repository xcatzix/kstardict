#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kstardict.py —— 基于qstardict和krunner(即:plasma search, ALT+SPACE打开)的翻译字典
依赖：KRunner + QStarDict + Python3 + Tkinter
Author: Xcatzix
Version: test-v-0.5.50
"""

import re
import subprocess
import threading
import queue
import time
import html as _html_mod  # 别名导入，避免与函数参数 html_text 同名遮蔽
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

# ---------- 浮窗视觉配置 ----------
BG_COLOR = "#d2e3de"
FG_COLOR = "#2a2f2e"
# FONT_FAMILY = "Noto Sans CJK SC"
FONT_FAMILY = "Ligconsolata Regular"
FONT_SIZE = 13
BORDER_COLOR = "#e53935"
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
POPUP_HEIGHT = 380
POPUP_WIDTH = 834
SCROLLBAR_WIDTH = 18
SCROLLBAR_COLOR = "#5a6062"
SCROLLBAR_BG = BG_COLOR
tw = POPUP_WIDTH
th = POPUP_HEIGHT
y = 126  # 浮窗在屏幕上y轴上的大概位置
"""
sw = popup.winfo_screenwidth()  # 获取屏幕宽度
x = (sw - tw) // 2  # 浮窗在屏幕上x轴上的大概位置
y = 126  # 浮窗在屏幕上y轴上的位置
popup.geometry(f"{tw}x{th}+{x}+{y}") #浮窗呈现在屏幕上真实位置由此行代码决定
"""

# --- 横线/字典名（UI 层，全部参数独立可调）--------------------
SEP_LINE_HEIGHT = 2
SEP_LINE_COLOR = "#ff5558"
SEP_WIDTH_EXTRA = 2
SEP_PAD_ABOVE = 10
SEP_PAD_BELOW = 6
DICT_NAME_FG = "#b3d38e"
DICT_NAME_SIZE = 9

# ---------- 全局状态 ----------
_pending_word = ""
_lock = threading.Lock()
_popup_queue = queue.Queue()
_tk_root = None
_active_popup = None
_popup_lock = threading.Lock()
_last_query = ""
MIN_WORD_LEN = 1


def _query_tr():
    """ftpmotd: do not modify this block"""
    global _last_query
    _last_query = "kstArdict"

    # 通过qdbus将KRunner输入框重置为'tr '(独立线程，避免 NoReply).
    def _run():
        try:
            subprocess.run(
                ["qdbus", "org.kde.krunner", "/App", "query", "tr "],
                capture_output=True,
                timeout=1,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _dismiss_krunner():
    # 温和关闭KRunner窗口(不杀进程)，使用D-Bus.

    def _run():
        try:
            r = subprocess.run(
                ["qdbus", "org.kde.krunner", "/App", "toggleDisplay"],
                capture_output=True,
                timeout=2,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass

    # don't using it, it can make problem...
    # threading.Thread(target=_run, daemon=True).start()


def strip_html(html_text: str):
    """
    HTML 清洗 + 格式化。返回 (main_text, dict_name)。
    """
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

    _half_to_full = {
        ",": "，",
        ";": "；",
        ":": "：",
        "!": "！",
        "?": "？",
    }
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
    """调用 QStarDict D-Bus 翻译。返回 (main_text, dict_name)；失败返回 ("", None)。"""
    try:
        bus = dbus.SessionBus()
        proxy = bus.get_object(QSTAR, QOBJ)
        iface = dbus.Interface(proxy, QSTAR)
    except dbus.exceptions.DBusException:
        return "", None

    for attempt in range(retries + 1):
        try:
            html = iface.translateHtml(word)
        except dbus.exceptions.DBusException:
            html = ""
        main_text, dict_name = strip_html(html)
        if main_text:
            return main_text, dict_name
        if attempt < retries:
            time.sleep(0.2)
    return "", None


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


def _classify_lines(text):
    """
    将译文按行分类，返回 [(line, color_key), ...]。
    color_key: 'title' | 'defn' | 'meta'
      - title: 首行（单词+音标）
      - meta: 以 [ 开头的标签行，如 [时态] [级别]
      - defn: 其余（词性释义行等）
    """
    if not text:
        return []
    lines = text.splitlines()
    out = []
    first = True
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if first:
            out.append((ln, "title"))
            first = False
        elif (
            s.startswith("[")
            and s.endswith("]")
            or (s.startswith("[") and not re.match(r"^[a-zA-Z]+[\.\)]", s))
        ):
            # [时态] / [级别] 等纯标签行 → meta
            out.append((ln, "meta"))
        else:
            out.append((ln, "defn"))
    return out


def _tk_worker():
    """唯一 Tk 主线程：跑 mainloop，所有 Tk 操作在此执行。"""
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

                _destroy_popup()

                if isinstance(payload, tuple):
                    main_text, dict_name = payload
                else:
                    main_text, dict_name = payload, None

                color_runs = _classify_lines(main_text)

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

                text_frame = tk.Frame(content, bg=BG_COLOR)
                text_frame.pack(fill="both", expand=True, padx=16, pady=12)
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

                # ---- 三类颜色 tag ----
                label.tag_configure("title", foreground="#dd4144")
                label.tag_configure("defn", foreground="#303534")
                label.tag_configure("meta", foreground="#f75457")

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

                scroll_indicator = tk.Label(
                    text_frame,
                    text="\u25bc",
                    bg=BG_COLOR,
                    fg="#fe2528",
                    font=(FONT_FAMILY, 7),
                    cursor="arrow",
                )
                scroll_indicator.grid(row=1, column=1, sticky="s", pady=(2, 0))

                def _on_scroll_down():
                    label.yview_scroll(1, "units")

                scroll_indicator.bind("<Button-1>", lambda e: _on_scroll_down())

                # 逐行插入 + 上色
                label.config(state="normal")
                label.delete("1.0", "end")
                for idx, (line, ckey) in enumerate(color_runs):
                    if idx > 0:
                        label.insert("end", "\n", ckey)
                    label.insert("end", line, ckey)
                label.config(state="disabled")

                # =====横线 + 字典名(仅当有字典名时, 暂未使用)=====
                if dict_name:
                    popup.update_idletasks()

                    spacer_above = tk.Frame(
                        popup, height=SEP_PAD_ABOVE, bg=BG_COLOR, bd=0
                    )
                    label.window_create("end", window=spacer_above)

                    sep_box = tk.Frame(popup, bg=BG_COLOR, bd=0)
                    sep_box.pack_propagate(False)

                    dn = tk.Label(
                        sep_box,
                        text=dict_name,
                        font=(FONT_FAMILY, DICT_NAME_SIZE),
                        fg=DICT_NAME_FG,
                        bg=BG_COLOR,
                        anchor="w",
                        padx=0,
                        pady=0,
                        bd=0,
                    )
                    dn.pack(anchor="w")

                    popup.update_idletasks()
                    dn_w = dn.winfo_reqwidth()
                    if dn_w <= 0:
                        dn_w = int(len(dict_name) * DICT_NAME_SIZE * 0.6)
                    sep_w = max(dn_w + SEP_WIDTH_EXTRA, 4)
                    sep = tk.Frame(
                        sep_box,
                        height=SEP_LINE_HEIGHT,
                        width=sep_w,
                        bg=SEP_LINE_COLOR,
                        bd=0,
                    )
                    sep.pack(anchor="w")

                    label.config(state="normal")
                    label.window_create("end", window=sep_box)
                    label.config(state="disabled")

                    spacer_below = tk.Frame(
                        popup, height=SEP_PAD_BELOW, bg=BG_COLOR, bd=0
                    )
                    label.config(state="normal")
                    label.window_create("end", window=spacer_below)
                    label.config(state="disabled")

                # ======定位浮窗======
                # tw = POPUP_WIDTH
                # th = POPUP_HEIGHT
                sw = popup.winfo_screenwidth()  # 获取屏幕宽度
                x = (sw - tw) // 2
                # y = 80
                popup.geometry(f"{tw}x{th}+{x}+{y}")
                popup.focus_force()

                if not _tk_exists(popup):
                    continue

                # 收集浮窗内所有可聚焦子部件，用于 FocusOut 内部判定
                fwidgets = set()
                try:
                    fwidgets.update(
                        [popup, content, text_frame, label, scrollbar, scroll_indicator]
                    )
                except Exception:
                    pass

                def _make_close(p):
                    def _close(event=None):
                        global _active_popup, fwidgets
                        if p != _active_popup:
                            return
                        # FocusOut 时：若焦点仍在浮窗内部子部件，不关闭
                        if event is not None and getattr(event, "type", None) == 10:
                            try:
                                new_focus = p.focus_get()
                            except tk.TclError:
                                new_focus = None
                            if new_focus is not None and (
                                new_focus == p or new_focus in fwidgets
                            ):
                                return
                        try:
                            p.destroy()
                        except tk.TclError:
                            pass
                        _active_popup = None
                        _query_tr()
                        _dismiss_krunner()

                    return _close

                close_fn = _make_close(popup)
                # 仅 popup 自身绑 FocusOut（避免子部件内部切换误触）
                """
                popup.bind("<Escape>", close_fn)
                popup.bind("<FocusOut>", close_fn)
                """

                def _on_mousewheel(event):
                    if not scrollbar.winfo_viewable():
                        return
                    if event.num == 4:
                        label.yview_scroll(-1, "units")
                    elif event.num == 5:
                        label.yview_scroll(1, "units")
                    return "break"

                for widget in (popup, content, text_frame, label, scroll_indicator):
                    widget.bind("<Button-4>", _on_mousewheel)
                    widget.bind("<Button-5>", _on_mousewheel)

                _active_popup = popup
        except queue.Empty:
            pass
        _tk_root.after(4, _process_queue)

    _tk_root.after(0, _process_queue)
    _tk_root.mainloop()


def show_floating_popup(payload):
    """入队弹窗请求。payload 可为 str 或 (main_text, dict_name) 元组。"""
    _popup_queue.put(payload)


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
        len_q = len(q)

        # ---- BackSpace 检测：用户删到 trigger 本身 ----
        if _last_query and _last_query != "kstArdict" and len(q) < len(_last_query):
            with _lock:
                _pending_word = ""
            _popup_queue.put(None)
            _destroy_popup()
            _query_tr()
            # 返回使用提示，让 KRunner 界面不为空
            """ ftpmotd: don't modify this block..."""
            return [
                (
                    "stardict-hint",
                    "Kstardict Web",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": ">'https://www.kstardict.com'"},
                )
            ]

        if _last_query == "kstArdict":
            _last_query = ""
        else:
            _last_query = q

        # ---- 仅 trigger 本身（无单词） → 返回提示 ----
        if q == TRIGGER + "  ":
            return [
                (
                    "stardict-hint",
                    "输入 tr wOrd. 进行翻译",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": "tr -h.查询说明"},
                )
            ]

        # ---- 匹配 tr <单词>. ----
        m = re.match(rf"^{re.escape(TRIGGER)}\s*(.+)\.\s*$", q)
        if not m:
            # 处于输入中（有 trigger 前缀但尚未以 . 结尾），返回提示
            if q.startswith(TRIGGER):
                return [
                    (
                        "stardict-hint",
                        "输入[tr wOrd.]进行翻译",
                        "accessories-dictionary",
                        100,
                        1.0,
                        {"subtext": "tr hello."},
                    )
                ]
            with _lock:
                _pending_word = ""
            return []

        word = m.group(1).rstrip(".")
        if not word or len(word) < MIN_WORD_LEN:
            with _lock:
                _pending_word = ""
            """ftpmotd: don't modify this block"""
            return [
                (
                    "stardict-hint",
                    f"单词至少{MIN_WORD_LEN}字母,符号未收录",
                    "accessories-dictionary",
                    100,
                    1.0,
                    {"subtext": ">'https://www.kstardict.com'提交"},
                )
            ]

        with _lock:
            _pending_word = word

        main_text, dict_name = qstardict_translate(word)

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

        show_floating_popup((main_text, dict_name))
        preview = main_text
        if len(preview) > 4800:
            preview = preview[:4800] + "..."
        return [
            (
                f"stardict-{word}",
                # preview.splitlines()[0] if preview else word,
                # f"{len_q}",
                "译文区支持滚轮下拉",
                "accessories-dictionary",
                100,
                1.0,
                {"subtext": "按Esc或丢失焦点以关闭"},
            )
        ]

    @dbus.service.method("org.kde.krunner1", in_signature="", out_signature="")
    def Teardown(self):
        """KRunner 失去焦点/关闭时调用。"""
        _destroy_popup()
        _dismiss_krunner()

    @dbus.service.method("org.kde.krunner1", in_signature="ss", out_signature="")
    def Run(self, id, action_id):
        """用户选中匹配项按回车时调用：关闭浮窗 + 退出 KRunner。"""
        _dismiss_krunner()


if __name__ == "__main__":
    DBusGMainLoop(set_as_default=True)
    StarDictRunner()
    GLib.MainLoop().run()
