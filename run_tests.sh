#!/bin/bash
# Test runner script for Reel v2 using uv

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Running Reel v2 Test Suite${NC}\n"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv is not installed${NC}"
    echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    uv venv .venv
fi

# Install dev dependencies if needed
echo -e "${YELLOW}Installing/updating dev dependencies...${NC}"
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt

# Set test environment variables
export TESTING=True
export SECRET_KEY=test-secret-key-for-testing-only
export JWT_SECRET_KEY=test-jwt-secret-key-for-testing-only
export DATABASE_URL=sqlite:///:memory:

# Parse command line arguments
COVERAGE=false
VERBOSE=false
MARKER=""
TEST_PATH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --coverage|-c)
            COVERAGE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --marker|-m)
            MARKER="$2"
            shift 2
            ;;
        --integration)
            MARKER="integration"
            shift
            ;;
        --unit)
            MARKER="unit"
            shift
            ;;
        --path|-p)
            TEST_PATH="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -c, --coverage    Run tests with coverage report"
            echo "  -v, --verbose      Verbose output"
            echo "  -m, --marker      Run tests with specific marker (e.g., unit, integration)"
            echo "  --integration     Run only integration tests"
            echo "  --unit            Run only unit tests (exclude integration)"
            echo "  -p, --path        Run tests from specific path"
            echo "  -h, --help        Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                          # Run all tests"
            echo "  $0 -c                       # Run with coverage"
            echo "  $0 --unit                   # Run only unit tests"
            echo "  $0 --integration            # Run only integration tests"
            echo "  $0 -m unit                  # Run only unit tests (alternative)"
            echo "  $0 -p tests/test_plugins     # Run plugin tests"
            echo "  $0 -c --integration         # Run integration tests with coverage"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Build pytest command (use venv's pytest)
PYTEST_CMD=".venv/bin/pytest"

if [ "$VERBOSE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD -v"
else
    PYTEST_CMD="$PYTEST_CMD"
fi

if [ "$COVERAGE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=. --cov-report=term-missing --cov-report=html"
fi

if [ -n "$MARKER" ]; then
    PYTEST_CMD="$PYTEST_CMD -m $MARKER"
fi

if [ -n "$TEST_PATH" ]; then
    PYTEST_CMD="$PYTEST_CMD $TEST_PATH"
else
    PYTEST_CMD="$PYTEST_CMD tests/"
fi

echo -e "${YELLOW}Running: $PYTEST_CMD${NC}\n"

# Run tests
if eval "$PYTEST_CMD"; then
    echo -e "\n${GREEN}✓ All tests passed!${NC}"
    
    if [ "$COVERAGE" = true ]; then
        echo -e "${GREEN}Coverage report generated in htmlcov/index.html${NC}"
    fi
    
    exit 0
else
    echo -e "\n${RED}✗ Tests failed${NC}"
    exit 1
fi


