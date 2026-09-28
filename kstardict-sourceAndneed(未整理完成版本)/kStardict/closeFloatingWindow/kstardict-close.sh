#!/bin/bash
# kstardict-close.sh —— 通过 DBus 关闭 kstardict 浮窗
# 用法：绑定到桌面环境全局快捷键（推荐 Ctrl+Shift+Q 或 Super+Esc，避免抢占裸 Esc）
# 依赖：dbus-send（通常随 dbus 包提供）
set -e
dbus-send --session --dest=org.kstardict.popup \
  /org/kstardict/popup \
  org.kstardict.popup.ClosePopup
