#!/usr/bin/env python3
"""camsys — запуск GUI без консольного окна (Windows).

Этот файл можно запускать двойным кликом — Windows ассоциирует .pyw
с pythonw.exe который не открывает консоль.
"""
import sys
import os

# Гарантируем что текущая директория — папка скрипта
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, script_dir)

# Запускаем main с аргументом gui
sys.argv = ['main.py', 'gui']
exec(open('main.py', encoding='utf-8').read())
