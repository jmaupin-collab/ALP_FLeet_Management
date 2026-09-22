#!/bin/bash
# Script to cleanly restart the backend

echo "🔄 Restarting Fleet Management Backend..."

# Kill all uvicorn/backend processes
echo "Stopping old backend processes..."
pkill -f "uvicorn app.main" 2>/dev/null || true
sleep 2

# Verify all stopped
if lsof -ti:9000 >/dev/null 2>&1; then
    echo "⚠️  Port 9000 still in use, force killing..."
    kill -9 $(lsof -ti:9000) 2>/dev/null || true
    sleep 1
fi

# Start backend
cd "$(dirname "$0")/backend"
echo "Starting backend on port 9000..."
nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 9000 --reload > /tmp/fleet_backend.log 2>&1 &
BACKEND_PID=$!

sleep 3

# Check if running
if lsof -ti:9000 >/dev/null 2>&1; then
    echo "✅ Backend started successfully (PID: $BACKEND_PID)"
    echo "📝 API Docs: http://127.0.0.1:9000/docs"
    echo "📋 Logs: tail -f /tmp/fleet_backend.log"
    echo ""
    echo "Test credentials:"
    echo "  Email: admin@example.com"
    echo "  Password: ChangeMe123!"
else
    echo "❌ Backend failed to start. Check logs:"
    echo "  cat /tmp/fleet_backend.log"
    exit 1
fi
