#!/bin/sh
# PG* environment / PGSERVICE must identify the backup role and source database.
set -eu
umask 077
dest=${1:?Supply an existing private backup directory}
test -d "$dest"
tmp=$(mktemp "$dest/.brunohost.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
pg_dump --format=custom --no-password --file="$tmp"
pg_restore --list "$tmp" > /dev/null
final="$dest/brunohost-$(date -u +%Y%m%dT%H%M%SZ)-${tmp##*.}.dump"
mv "$tmp" "$final"
printf '%s\n' "$final"
