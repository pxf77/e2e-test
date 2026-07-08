.PHONY: install install-uv test validate-schemas ci-check clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

install-uv:
	uv sync --all-extras

test:
	$(PYTHON) -m pytest tests/ -v --cov=src/e2e_agent --cov-report=term-missing

validate-schemas:
	$(PYTHON) tools/validate_schemas.py

ci-check:
	$(PYTHON) tools/ci_rule_check.py

install-playwright:
	$(PYTHON) -m playwright install chromium

smoke:
	$(PYTHON) -c "from e2e_agent.graph.graph import build_graph; g = build_graph(':memory:'); print('graph OK')"
	$(PYTHON) -c "from e2e_agent.skills.loader import SkillPackageLoader; print('skills:', SkillPackageLoader().list_skills())"
	$(PYTHON) -c "from e2e_agent.llm.wrapper import LLMWrapper; LLMWrapper(); print('llm wrapper OK')"

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	rm -f e2e_agent.db
