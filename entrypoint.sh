#!/bin/sh
set -e

# When started as root, hand the data directory to the app user and drop privileges.
# This makes bind mounts and pre-existing volumes owned by root work out of the box.
if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-10001}"
    PGID="${PGID:-10001}"
    mkdir -p "$DATA_DIR"
    chown -R "$PUID:$PGID" "$DATA_DIR" || echo "warning: could not change owner of $DATA_DIR" >&2
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
