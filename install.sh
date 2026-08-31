#!/usr/bin/env bash
# ==========================================
# ---*-coding: utf-8-*---
# Application's name: kstardict
# Author: xcatzix
# mailto: 343451012@qq.com
# ==========================================

if [[ "$1" == '-h' ]] || [[ -z "$1" ]]; then
	echo "Using: install -(i|r) install or remove"
	return
fi

if [[ "$1" == '-i' ]]; then
	chmod +x ./kstardict.py
	cp ./kstardict.py ~/.local/bin/
	cp ./*.desktop ~/.local/share/krunner/dbusplugins/
	cp ./*.service ~/.local/share/dbus-1/services/com.yourname.krunner-stardict.service
	kbuildsycoca6
	kquitapp6 krunner
	krunner --daemon &
fi

if [[ "$1" == '-r' ]]; then
	rm ~/.local/bin/kstardict.py
	rm ~/.local/share/krunner/dbusplugins/com.yourname.krunner-stardict.desktop
	rm ~/.local/share/dbus-1/services/com.yourname.krunner-stardict.service
	pkill -f kstardict.py
	kbuildsycoca6
	kquitapp6 krunner
	krunner --daemon &
fi