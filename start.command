#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "Окружение не найдено. Выполните инструкции из README.md"
  read -k 1
  exit 1
fi
.venv/bin/python start_shared_events.py || exit 1
.venv/bin/python manage.py runserver &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null' EXIT
sleep 2
open http://127.0.0.1:8000/
wait "$server_pid"
