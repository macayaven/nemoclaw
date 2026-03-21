#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Running pre-push checks...${NC}"

cd "$(git rev-parse --show-toplevel)/tests"

echo -n "  Linting (ruff)... "
uv run ruff check . --quiet && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Formatting (ruff)... "
uv run ruff format --check . --quiet && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Import sorting (isort)... "
uv run isort --check-only . --quiet && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Type checking (mypy)... "
uv run mypy . --ignore-missing-imports --no-error-summary 2>/dev/null && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Docstrings (pydocstyle)... "
uv run pydocstyle . --convention=google --add-ignore=D100,D104 --count 2>/dev/null && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Syntax validation... "
python3 -c "
import ast, glob, sys
errors = []
for f in glob.glob('**/*.py', recursive=True):
    try:
        ast.parse(open(f).read())
    except SyntaxError as e:
        errors.append(f'{f}: {e}')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
" && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -n "  Test collection... "
uv run pytest . --collect-only -q --no-header 2>/dev/null | tail -1 | grep -q "test" && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAIL${NC}"; exit 1; }

echo -e "\n${GREEN}All pre-push checks passed!${NC}"
