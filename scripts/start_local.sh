#!/bin/bash
set -e

PORT=5002
LOG_FILE="data/app.log"
PID_FILE="data/server.pid"

echo "=== Managing Local Server on port $PORT ==="

# 1. Kill whatever this script started last time, via the recorded pid file.
# uvicorn --reload runs as a parent "reloader" process that forks a child to
# actually serve requests -- $PID_FILE holds the *parent's* pid (that's what
# `nohup ... & ; echo $!` captures), and the parent doesn't always die just
# because its child gets killed by the port check below. So kill the
# recorded pid AND any of its children explicitly, before falling back to
# the port-based check for anything this pid file doesn't know about
# (e.g. a server started some other way, or a stale/missing pid file).
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if [ -n "$OLD_PID" ] && ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  Found previous server (pid $OLD_PID) from $PID_FILE. Killing it..."
        CHILD_PIDS=$(pgrep -P "$OLD_PID" 2>/dev/null || true)
        [ -n "$CHILD_PIDS" ] && kill -9 $CHILD_PIDS 2>/dev/null || true
        kill -9 "$OLD_PID" 2>/dev/null || true
        sleep 1
        echo "✅  Killed previous server."
    fi
    rm -f "$PID_FILE"
fi

# 2. Check for anything still bound to the port (catches servers not
# started by this script, or anything the pid-based kill above missed).
PID=$(lsof -ti :$PORT || true)
if [ -n "$PID" ]; then
    echo "⚠️  Port $PORT is still in use by PID $PID. Killing it..."
    kill -9 $PID
    sleep 1
    echo "✅  Killed old process."
else
    echo "ℹ️  Port $PORT is free."
fi

# 3. Cleanup old log and ensure dir exists
mkdir -p data
if [ -f "$LOG_FILE" ]; then
    echo "🧹 Rotated old log."
    mv "$LOG_FILE" "${LOG_FILE}.old"
fi
touch "$LOG_FILE"

# 4. Start Server
echo "🚀 Starting uvicorn..."
nohup uv run python -m uvicorn app.implementation.main:app --port $PORT --reload > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"
echo "ℹ️  Process-ID: $NEW_PID"

# 5. Wait and Verify
echo "⏳ Waiting for startup (5s)..."
for i in {1..5}; do
    if ! ps -p $NEW_PID > /dev/null; then
        echo "❌ Server died immediately. Log contents:"
        cat "$LOG_FILE"
        exit 1
    fi
    sleep 1
done

echo "✅ Server seems to be running."
echo "📋 Log output (head):"
echo "--------------------------------"
head -n 10 "$LOG_FILE"
echo "--------------------------------"

echo "🔎 Testing connectivity..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/ | grep -q "200"; then
    echo "✅ Health Check Passed: http://localhost:$PORT/"
else
    echo "⚠️  Health Check Failed. Server might still be initializing or unreachable."
    echo "Full response:"
    curl -v http://localhost:$PORT/ || true
fi
