#!/bin/zsh
cd "$(dirname "$0")" || exit 1
.venv/bin/python start_telegram_local.py
echo "Нажмите любую клавишу, чтобы закрыть окно."
read -k 1
