#!/bin/bash
set -e

REPO="https://github.com/nsfwhusnain-coder/pincer"
INSTALL_DIR="$HOME/.pincer"
VENV="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"

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
    echo "→ Creating virtual environment..."
    python3 -m venv "$VENV"
fi

echo "→ Installing dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$INSTALL_DIR/repo/requirements.txt" -q

echo "→ Creating launcher..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/pincer" << 'LAUNCHER'
#!/bin/bash
exec "$HOME/.pincer/venv/bin/python" "$HOME/.pincer/repo/agent.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/pincer"

# Add to PATH if not already there
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
    echo "✓ Added ~/.local/bin to PATH. Restart your terminal or run: source ~/.zshrc"
fi

echo ""
echo "✓ Pincer installed. Type 'pincer' to start."
