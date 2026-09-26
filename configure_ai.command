#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "Окружение не найдено. Выполните инструкции из README.md"
  read -k 1
  exit 1
fi
.venv/bin/python configure_ai.py
echo "Нажмите любую клавишу, чтобы закрыть окно."
read -k 1
