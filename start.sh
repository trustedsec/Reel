#!/bin/bash
# Reel v2 Startup Script
# Handles app-level setup (venv, deps, .env, directories, DB) and starts servers.
# Assumes host dependencies are already installed via deploy.sh.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Parse command line arguments
SKIP_SETUP=false
SKIP_INIT=false
RESET_DB=false
ADMIN_ONLY=false
PHISHING_ONLY=false
WITH_CADDY=false
PRELOAD_ML=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        --skip-init)
            SKIP_INIT=true
            shift
            ;;
        --reset-db)
            RESET_DB=true
            shift
            ;;
        --admin-only)
            ADMIN_ONLY=true
            shift
            ;;
        --phishing-only)
            PHISHING_ONLY=true
            shift
            ;;
        --with-caddy)
            WITH_CADDY=true
            shift
            ;;
        --preload-ml)
            PRELOAD_ML=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-setup     Skip venv/deps/dirs (just load .env and start servers)"
            echo "  --skip-init      Skip database initialization"
            echo "  --reset-db       Reset database (delete and recreate)"
            echo "  --admin-only     Start only the admin server"
            echo "  --phishing-only  Start only the phishing server"
            echo "  --with-caddy     Start Caddy via Docker (local testing only)"
            echo "  --preload-ml     Pre-download phishing detection model (~1.3GB)"
            echo "  -h, --help       Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                    # Full setup and start both servers"
            echo "  $0 --skip-setup       # Skip setup, just start servers"
            echo "  $0 --skip-init        # Skip DB init, still set up venv/deps"
            echo "  $0 --reset-db         # Reset database and start servers"
            echo "  $0 --admin-only       # Start only admin server"
            echo "  $0 --with-caddy       # Start with Caddy via Docker (local testing)"
            echo "  $0 --preload-ml       # Pre-download phishing detection model"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Reel v2 Startup Script            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# ──────────────────────────────────────────────
# Step 0: Environment variables and secrets
# ──────────────────────────────────────────────
echo -e "${CYAN}[0/7]${NC} Checking environment variables..."

# Load .env file if it exists
if [ -f ".env" ]; then
    echo -e "${GREEN}✓ .env file found${NC}"
    set -a
    source .env
    set +a
else
    echo -e "${YELLOW}  No .env file found, creating from template...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        # Override dev defaults for production
        sed -i 's/^FLASK_ENV=development/FLASK_ENV=production/' .env 2>/dev/null || true
        sed -i 's/^FLASK_DEBUG=True/FLASK_DEBUG=False/' .env 2>/dev/null || true
        echo -e "${GREEN}✓ Created .env from .env.example (set to production)${NC}"
    else
        touch .env
        echo -e "${YELLOW}  Created empty .env${NC}"
    fi
    set -a
    source .env
    set +a
fi

# Check for required secrets and generate if missing
NEEDS_ENV_UPDATE=false

if [ -z "$SECRET_KEY" ] || [ "$SECRET_KEY" = "your-secret-key-change-this" ] || [ "$SECRET_KEY" = "dev-key-change-this" ]; then
    echo -e "${YELLOW}  SECRET_KEY not set or using default -- generating...${NC}"
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -hex 32)
    if [ -z "$SECRET_KEY" ]; then
        echo -e "${RED}✗ Failed to generate SECRET_KEY${NC}"
        exit 1
    fi
    echo "SECRET_KEY=$SECRET_KEY" >> .env
    NEEDS_ENV_UPDATE=true
    echo -e "${GREEN}✓ SECRET_KEY generated and saved to .env${NC}"
fi

if [ -z "$JWT_SECRET_KEY" ] || [ "$JWT_SECRET_KEY" = "your-jwt-secret-change-this" ] || [ "$JWT_SECRET_KEY" = "jwt-secret-change-this" ]; then
    echo -e "${YELLOW}  JWT_SECRET_KEY not set or using default -- generating...${NC}"
    JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -hex 32)
    if [ -z "$JWT_SECRET_KEY" ]; then
        echo -e "${RED}✗ Failed to generate JWT_SECRET_KEY${NC}"
        exit 1
    fi
    echo "JWT_SECRET_KEY=$JWT_SECRET_KEY" >> .env
    NEEDS_ENV_UPDATE=true
    echo -e "${GREEN}✓ JWT_SECRET_KEY generated and saved to .env${NC}"
fi

if [ -z "$PHISHING_HOST" ]; then
    PHISHING_HOST="127.0.0.1"
    echo "PHISHING_HOST=$PHISHING_HOST" >> .env
    NEEDS_ENV_UPDATE=true
    echo -e "${GREEN}✓ PHISHING_HOST set to 127.0.0.1 in .env${NC}"
fi

# Reload .env if we just updated it
if [ "$NEEDS_ENV_UPDATE" = true ] && [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Export variables for child processes
export SECRET_KEY
export JWT_SECRET_KEY

if [ "$NEEDS_ENV_UPDATE" = false ]; then
    echo -e "${GREEN}✓ Environment variables configured${NC}"
fi
echo ""

# ──────────────────────────────────────────────
# Steps 1-4: App setup (skip with --skip-setup)
# ──────────────────────────────────────────────
if [ "$SKIP_SETUP" = false ]; then

    # Step 1: Check if uv is installed
    echo -e "${CYAN}[1/7]${NC} Checking for uv..."
    if ! command -v uv &> /dev/null; then
        echo -e "${RED}✗ uv is not installed${NC}"
        echo ""
        echo "Run deploy.sh first to install host dependencies:"
        echo "  ./deploy.sh"
        echo ""
        echo "Or install uv manually:"
        echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    echo -e "${GREEN}✓ uv is installed${NC}"
    echo ""

    # Step 2: Create/verify virtual environment
    echo -e "${CYAN}[2/7]${NC} Setting up virtual environment..."
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}  Creating virtual environment...${NC}"
        uv venv .venv
        echo -e "${GREEN}✓ Virtual environment created${NC}"
    else
        echo -e "${GREEN}✓ Virtual environment already exists${NC}"
    fi
    echo ""

    # Step 3: Install dependencies
    echo -e "${CYAN}[3/7]${NC} Installing Python dependencies..."
    uv pip install --python .venv/bin/python -r requirements.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}"
    echo ""

    # Step 4: Playwright browsers
    echo -e "${CYAN}[4/7]${NC} Ensuring Playwright browsers (Chromium)..."
    if uv run playwright install chromium 2>/dev/null; then
        echo -e "${GREEN}✓ Playwright browsers ready${NC}"
    else
        echo -e "${YELLOW}⚠  Playwright browsers not installed (credential proxy may not work)${NC}"
        echo -e "${YELLOW}   Run manually: uv run playwright install chromium${NC}"
    fi
    echo ""

    # Step 4b: Pre-load ML model (optional)
    if [ "$PRELOAD_ML" = true ]; then
        echo -e "${CYAN}[4b/7]${NC} Pre-loading phishing detection model (~1.3GB)..."
        if uv run python -c "
from transformers import AutoModelForSequenceClassification, AutoTokenizer
print('Downloading BERT phishing model...')
AutoTokenizer.from_pretrained('ealvaradob/bert-finetuned-phishing')
AutoModelForSequenceClassification.from_pretrained('ealvaradob/bert-finetuned-phishing')
print('Done.')
" 2>/dev/null; then
            echo -e "${GREEN}✓ Phishing detection model cached${NC}"
        else
            echo -e "${YELLOW}⚠  Preload failed (transformers not installed or download error)${NC}"
            echo -e "${YELLOW}   Model will download on first use of phishing detector plugin${NC}"
        fi
        echo ""
    fi

else
    echo -e "${CYAN}[1-4/7]${NC} Skipping setup (--skip-setup)${NC}"
    echo ""
fi

# ──────────────────────────────────────────────
# Step 5: Create directories
# ──────────────────────────────────────────────
echo -e "${CYAN}[5/7]${NC} Ensuring directories exist..."
mkdir -p \
    storage/caddy/data \
    storage/caddy/config \
    storage/uploads \
    storage/templates \
    storage/assets \
    storage/temp \
    storage/template_previews \
    storage/backups \
    storage/plugins \
    instance \
    logs
echo -e "${GREEN}✓ Directories ready${NC}"
echo ""

# ──────────────────────────────────────────────
# Step 6: Database initialization
# ──────────────────────────────────────────────
if [ "$SKIP_INIT" = false ]; then
    echo -e "${CYAN}[6/7]${NC} Checking database..."

    # Handle database reset
    if [ "$RESET_DB" = true ]; then
        if [ -f "instance/reel.db" ]; then
            echo -e "${YELLOW}  Resetting database...${NC}"
            rm -f instance/reel.db
            echo -e "${GREEN}✓ Existing database removed${NC}"
        elif [ -f "reel.db" ]; then
            echo -e "${YELLOW}  Resetting database...${NC}"
            rm -f reel.db
            echo -e "${GREEN}✓ Existing database removed${NC}"
        else
            echo -e "${YELLOW}  No existing database to reset${NC}"
        fi
    fi

    # Check if database exists
    if [ -f "instance/reel.db" ] || [ -f "reel.db" ]; then
        echo -e "${GREEN}✓ Database already exists${NC}"
        if [ "$RESET_DB" = false ]; then
            echo -e "${YELLOW}  Use --reset-db to reset or --skip-init to skip this check${NC}"
        fi
    else
        echo -e "${YELLOW}  Initializing database...${NC}"
        echo -e "${YELLOW}  This will create the default admin user (admin/admin123)${NC}"
        echo ""

        uv run python -m cli init --force || {
            echo -e "${RED}✗ Database initialization failed${NC}"
            echo -e "${YELLOW}  You may need to run: uv run python -m cli init${NC}"
            exit 1
        }

        echo -e "${GREEN}✓ Database initialized${NC}"
        echo -e "${YELLOW}⚠  Default credentials: admin / admin123${NC}"
        echo -e "${YELLOW}⚠  Please change the password after first login!${NC}"
    fi
else
    echo -e "${CYAN}[6/7]${NC} Skipping database initialization (--skip-init)${NC}"
fi
echo ""

# ──────────────────────────────────────────────
# Step 7: Start Caddy via Docker (local testing only)
# ──────────────────────────────────────────────
if [ "$WITH_CADDY" = true ]; then
    echo -e "${CYAN}[7/7]${NC} Starting Caddy via Docker (local testing)..."

    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}⚠  Docker not found, skipping Caddy startup${NC}"
        echo -e "${YELLOW}   Install Docker to use Caddy locally: https://docs.docker.com/get-docker/${NC}"
    elif ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null 2>&1; then
        echo -e "${YELLOW}⚠  Docker Compose not found, skipping Caddy startup${NC}"
    else
        # Create Caddy storage directories
        mkdir -p storage/caddy/data storage/caddy/config

        # Check if Caddy is already running
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "caddy"; then
            echo -e "${GREEN}✓ Caddy container is already running${NC}"
        else
            echo -e "${YELLOW}  Starting Caddy container...${NC}"

            # Use docker compose (newer) or docker-compose (older)
            if docker compose version &> /dev/null 2>&1; then
                COMPOSE_CMD="docker compose"
            else
                COMPOSE_CMD="docker-compose"
            fi

            $COMPOSE_CMD -f docker-compose.caddy.yml up -d || {
                echo -e "${RED}✗ Failed to start Caddy${NC}"
                echo -e "${YELLOW}  Continuing without Caddy (campaign activation may fail)${NC}"
            }

            # Wait for Caddy API to be ready
            echo -e "${YELLOW}  Waiting for Caddy API to be ready...${NC}"
            MAX_RETRIES=30
            RETRY_COUNT=0
            while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
                if curl -s http://localhost:2019/config/ > /dev/null 2>&1; then
                    echo -e "${GREEN}✓ Caddy API is ready${NC}"
                    break
                fi
                RETRY_COUNT=$((RETRY_COUNT + 1))
                sleep 1
            done

            if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
                echo -e "${YELLOW}⚠  Caddy API not ready after ${MAX_RETRIES}s${NC}"
                echo -e "${YELLOW}   Continuing anyway (Caddy may still be starting)${NC}"
            fi
        fi
    fi
    echo ""
fi

# ──────────────────────────────────────────────
# Start servers
# ──────────────────────────────────────────────
echo -e "${CYAN}Starting servers...${NC}"
echo ""

if [ "$ADMIN_ONLY" = true ]; then
    echo -e "${GREEN}Starting admin server only...${NC}"
    echo -e "${CYAN}Admin UI: http://localhost:8000${NC}"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    echo ""
    uv run python app.py

elif [ "$PHISHING_ONLY" = true ]; then
    echo -e "${GREEN}Starting phishing server only...${NC}"
    echo -e "${CYAN}Phishing server: http://localhost:1234${NC}"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    echo ""
    uv run python -c "from app import create_app; create_app().run(host='0.0.0.0', port=1234, debug=True)"

else
    echo -e "${GREEN}Starting both servers...${NC}"
    echo ""
    echo -e "${CYAN}Admin UI: http://localhost:8000${NC}"
    echo -e "${CYAN}Phishing Server: http://localhost:1234${NC}"
    if [ "$WITH_CADDY" = true ]; then
        echo -e "${CYAN}Caddy Server: http://localhost:80 (API: http://localhost:2019)${NC}"
    fi
    echo ""
    echo -e "${YELLOW}Default login: admin / admin123${NC}"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop all servers${NC}"
    echo ""

    # Use the CLI start command which handles both servers
    uv run python -m cli start
fi
