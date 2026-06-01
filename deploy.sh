#!/bin/bash
# Reel v2 Host Deployment Script (Ubuntu/Debian)
# Installs system-level dependencies: Python, build tools, uv, Caddy, Playwright system libs.
# Idempotent -- safe to run multiple times. Run once per machine, then use ./start.sh.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Installs all host-level dependencies needed to run Reel v2."
            echo "Run once per machine, then use ./start.sh to launch the app."
            echo ""
            echo "  -h, --help   Show this help"
            exit 0
            ;;
    esac
done

echo -e "${CYAN}╔════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Reel v2 Host Deployment (Ubuntu/Debian)      ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════╝${NC}"
echo ""

# --- Determine sudo command ---
SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo &>/dev/null && SUDO="sudo"

# --- 1. OS check ---
echo -e "${CYAN}[1/7]${NC} Checking OS..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "$ID" in
        ubuntu) ;;
        debian) ;;
        *)
            echo -e "${RED}This script targets Ubuntu/Debian. Found: $ID${NC}"
            exit 1
            ;;
    esac
else
    echo -e "${RED}Cannot detect OS (no /etc/os-release).${NC}"
    exit 1
fi
echo -e "${GREEN}✓ $PRETTY_NAME detected${NC}"
echo ""

# --- 2. System packages ---
echo -e "${CYAN}[2/7]${NC} Installing system packages..."
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq \
    curl \
    ca-certificates \
    git \
    build-essential \
    gcc \
    python3 \
    python3-venv \
    python3-dev \
    libmagic1 \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https
echo -e "${GREEN}✓ System packages installed${NC}"
echo ""

# --- 3. Install uv ---
echo -e "${CYAN}[3/7]${NC} Checking uv..."
if ! command -v uv &>/dev/null; then
    echo -e "${YELLOW}  Installing uv...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
    if ! command -v uv &>/dev/null; then
        echo -e "${RED}✗ uv install failed or not on PATH. Add ~/.local/bin to PATH and retry.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ uv installed${NC}"
else
    echo -e "${GREEN}✓ uv already installed$(uv --version 2>/dev/null && echo "" || echo "")${NC}"
fi
export PATH="${HOME}/.local/bin:${PATH}"
echo ""

# --- 4. Install Caddy ---
echo -e "${CYAN}[4/7]${NC} Checking Caddy..."
if ! command -v caddy &>/dev/null; then
    echo -e "${YELLOW}  Adding Caddy APT repository and installing...${NC}"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq caddy
    echo -e "${GREEN}✓ Caddy installed${NC}"
else
    echo -e "${GREEN}✓ Caddy already installed${NC}"
fi
echo ""

# --- 5. Playwright system dependencies ---
echo -e "${CYAN}[5/7]${NC} Installing Playwright system dependencies..."
# Create a temporary venv to run playwright install-deps (needs the package available)
TEMP_VENV=$(mktemp -d)
uv venv "$TEMP_VENV" --quiet 2>/dev/null
uv pip install --python "$TEMP_VENV/bin/python" playwright --quiet 2>/dev/null
$SUDO "$TEMP_VENV/bin/python" -m playwright install-deps chromium 2>/dev/null && {
    echo -e "${GREEN}✓ Playwright system dependencies installed${NC}"
} || {
    echo -e "${YELLOW}⚠  Playwright system deps failed (credential proxy may not work)${NC}"
    echo -e "${YELLOW}   Run manually: sudo playwright install-deps chromium${NC}"
}
rm -rf "$TEMP_VENV"
echo ""

# --- 6. Make scripts executable ---
echo -e "${CYAN}[6/7]${NC} Setting file permissions..."
chmod +x start.sh 2>/dev/null || true
chmod +x run_tests.sh 2>/dev/null || true
echo -e "${GREEN}✓ Scripts are executable${NC}"
echo ""

# --- 7. Start Caddy ---
echo -e "${CYAN}[7/7]${NC} Starting Caddy server..."
if curl -s http://localhost:2019/config/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Caddy API is already running${NC}"
else
    caddy start --config "$(pwd)/Caddyfile.minimal" 2>/dev/null && {
        echo -e "${GREEN}✓ Caddy started (API: http://localhost:2019)${NC}"
    } || {
        echo -e "${YELLOW}⚠  Failed to start Caddy automatically${NC}"
        echo -e "${YELLOW}   Start it manually: caddy start --config $(pwd)/Caddyfile.minimal${NC}"
    }
fi
echo ""

# --- Done ---
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Host deployment complete.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Next steps:${NC}"
echo "  1. Start the app: ./start.sh"
echo "  2. See all options: ./start.sh --help"
echo ""
