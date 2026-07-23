#!/bin/bash
screen -dmS graphspy bash -c 'graphspy'
screen -dmS reel bash -c './start.sh --preload-ml'
sleep 2
echo "Reel started in screen session 'reel'"
echo "  Admin UI:       http://localhost:8000"
echo "  Phishing Server: http://localhost:1234"
echo "  Attach with:     screen -r reel"
exec tail -f /dev/null
