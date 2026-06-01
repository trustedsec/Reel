# Reel Phishing Framework - Testing and Development Commands

.PHONY: help test test-unit test-integration test-functional test-coverage install-dev lint format clean

help:  ## Show this help message
	@echo "Reel Testing Framework Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install-dev:  ## Install development dependencies
	uv pip install -r requirements.txt
	uv pip install -r requirements-dev.txt

test: test-unit test-integration test-functional  ## Run all tests

test-unit:  ## Run unit tests only
	pytest tests/unit/ -v --tb=short

test-integration:  ## Run integration tests only
	pytest tests/integration/ -v --tb=short

test-functional:  ## Run functional/end-to-end tests only
	pytest tests/functional/ -v --tb=short

test-coverage:  ## Run tests with coverage report
	uv run pytest --cov=. --cov-report=html --cov-report=term-missing --cov-fail-under=70

test-fast:  ## Run tests without coverage (faster)
	uv run pytest tests/ -x -v --tb=short --disable-warnings

test-uv:  ## Run all tests using uv (recommended)
	./run_tests.sh

test-uv-coverage:  ## Run tests with coverage using uv
	./run_tests.sh --coverage

test-models:  ## Run database model tests only
	pytest tests/unit/test_models.py -v

test-auth:  ## Run authentication tests only
	pytest tests/unit/test_auth.py -v

test-handlers:  ## Run campaign handler tests only
	pytest tests/unit/handlers/ -v

test-api:  ## Run API tests only
	pytest tests/integration/test_api_routes.py -v

test-admin:  ## Run admin interface tests only
	pytest tests/integration/test_admin_routes.py -v

test-phishing:  ## Run phishing route tests only
	pytest tests/integration/test_phishing_routes.py -v

test-lifecycle:  ## Run campaign lifecycle tests only
	pytest tests/functional/test_campaign_lifecycle.py -v

test-workflows:  ## Run user workflow tests only
	pytest tests/functional/test_user_workflows.py -v

lint:  ## Run code linting with flake8
	flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

format:  ## Format code with black
	black . --line-length=127 --skip-string-normalization

format-check:  ## Check code formatting without making changes
	black . --line-length=127 --skip-string-normalization --check

type-check:  ## Run type checking with mypy
	mypy . --ignore-missing-imports --show-error-codes

security-check:  ## Run security analysis with bandit
	bandit -r . -ll -x tests/,venv/

clean:  ## Clean up temporary files and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + || true
	find . -type f -name "*.pyc" -delete || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + || true
	find . -name ".coverage" -delete || true
	rm -f coverage.xml || true
	rm -rf htmlcov/ || true
	rm -rf .pytest_cache/ || true
	rm -rf .mypy_cache/ || true

test-db:  ## Test database operations and models
	pytest tests/unit/test_models.py tests/unit/test_auth.py -v --tb=short

test-handlers-all:  ## Test all campaign handlers
	pytest tests/unit/handlers/ tests/unit/test_validators.py -v

test-routes-all:  ## Test all route endpoints
	pytest tests/integration/ -v --tb=short

test-quick:  ## Run a quick smoke test
	pytest tests/unit/test_models.py::TestUser::test_create_user -v

test-debug:  ## Run tests with debugging output
	pytest tests/ -v --tb=long --capture=no

test-parallel:  ## Run tests in parallel (requires pytest-xdist)
	pytest tests/ -n auto --tb=short

# Database commands
db-init:  ## Initialize test database
	python -c "from app import create_admin_app; from shared.database import db; app = create_admin_app(); app.app_context().push(); db.create_all()"

db-reset:  ## Reset test database
	python -c "from app import create_admin_app; from shared.database import db; app = create_admin_app(); app.app_context().push(); db.drop_all(); db.create_all()"

# Development server commands
run-admin:  ## Run admin server in development mode
	python -c "from app import create_admin_app; create_admin_app().run(debug=True, host='127.0.0.1', port=8000)"

run-phishing:  ## Run phishing server in development mode
	python -c "from app import create_app; create_app().run(debug=True, host='0.0.0.0', port=1234)"

# Continuous testing
watch-tests:  ## Run tests continuously on file changes (requires pytest-watch)
	pytest-watch tests/ --clear --onpass="echo 'Tests passed!'" --onfail="echo 'Tests failed!'"

# Coverage analysis
coverage-html:  ## Generate HTML coverage report
	pytest --cov=. --cov-report=html tests/
	@echo "Coverage report generated in htmlcov/index.html"

coverage-xml:  ## Generate XML coverage report
	pytest --cov=. --cov-report=xml tests/

# Benchmarking
benchmark:  ## Run performance benchmarks (if implemented)
	pytest tests/ -m benchmark --benchmark-only

# Test data generation
generate-test-data:  ## Generate test data for manual testing
	python -c "from tests.conftest import *; print('Test data generation not yet implemented')"

# Pre-commit checks
pre-commit: format-check lint type-check test-fast  ## Run all pre-commit checks

ci-test: install-dev test-coverage lint security-check  ## Run full CI test suite

# Help for specific test categories
test-help:  ## Show detailed testing help
	@echo "Reel Testing Framework"
	@echo "====================="
	@echo ""
	@echo "Test Structure:"
	@echo "  tests/unit/          - Unit tests for isolated components"
	@echo "  tests/integration/   - Integration tests for API and routes"
	@echo "  tests/functional/    - End-to-end workflow tests"
	@echo ""
	@echo "Key Test Files:"
	@echo "  test_models.py       - Database model tests"
	@echo "  test_auth.py         - Authentication system tests"
	@echo "  test_validators.py   - Pydantic validation tests"
	@echo "  test_*_routes.py     - Route endpoint tests"
	@echo "  test_*_handlers.py   - Campaign handler tests"
	@echo ""
	@echo "Coverage Target: 80%+"
	@echo ""
	@echo "Example Commands:"
	@echo "  make test                    # Run all tests"
	@echo "  make test-unit               # Run only unit tests"
	@echo "  make test-coverage           # Run tests with coverage"
	@echo "  make test-models             # Test database models only"
	@echo "  make test-api                # Test API endpoints only"