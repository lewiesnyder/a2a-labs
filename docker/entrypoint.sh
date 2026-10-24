#!/bin/sh
# Optionally waits for $WAIT_FOR (comma-separated URLs), then execs the command.
set -e
if [ -n "$WAIT_FOR" ]; then
  for url in $(echo "$WAIT_FOR" | tr ',' ' '); do
    until python -c "import urllib.request,sys;urllib.request.urlopen(sys.argv[1],timeout=3)" "$url" 2>/dev/null; do
      echo "waiting for $url"; sleep 2
    done
  done
fi
exec "$@"
