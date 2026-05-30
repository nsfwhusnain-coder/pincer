#!/bin/bash
set -e
REPO="https://github.com/nsfwhusnain-coder/pincer"
INSTALL_DIR="$HOME/.pincer"
VENV="$INSTALL_DIR/venv"
echo "🦞 Installing Pincer..."
mkdir -p "$INSTALL_DIR"
if [ -d "$INSTALL_DIR/repo" ]; then
    echo "→ Updating..."
    cd "$INSTALL_DIR/repo" && git pull
else
    echo "→ Cloning..."
    git clone "$REPO" "$INSTALL_DIR/repo"
fi
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$INSTALL_DIR/repo/requirements.txt" -q
cat > /usr/local/bin/pincer << 'LAUNCHER'
#!/bin/bash
exec "$HOME/.pincer/venv/bin/python" "$HOME/.pincer/repo/agent.py" "$@"
LAUNCHER
chmod +x /usr/local/bin/pincer
echo "✓ Done. Type 'pincer' to start."
