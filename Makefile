.PHONY: install install-uv test validate-repository validate-docs validate-schemas validate-domains validate-workflows validate-runners validate-plugins boundary-check ci-check acceptance clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

install-uv:
	uv sync --all-extras

test:
	$(PYTHON) -m pytest tests/ -v --cov=src/e2e_agent --cov-report=term-missing

validate-repository:
	$(PYTHON) tools/validate_repository.py

validate-docs:
	$(PYTHON) tools/validate_docs.py

validate-schemas:
	$(PYTHON) tools/validate_schemas.py

validate-domains:
	$(PYTHON) tools/validate_domains.py

validate-workflows:
	$(PYTHON) tools/validate_workflows.py

validate-runners:
	$(PYTHON) tools/validate_runners.py

validate-plugins:
	$(PYTHON) tools/validate_plugins.py

boundary-check:
	$(PYTHON) tools/check_domain_boundaries.py

ci-check:
	$(PYTHON) tools/validate_repository.py
	$(PYTHON) tools/validate_docs.py
	$(PYTHON) tools/ci_rule_check.py
	$(PYTHON) tools/check_domain_boundaries.py

acceptance:
	$(PYTHON) tools/acceptance_matrix.py

install-playwright:
	$(PYTHON) -m playwright install chromium

smoke:
	$(PYTHON) -c "from e2e_agent.graph.graph import build_graph; g = build_graph(':memory:'); print('graph OK')"
	$(PYTHON) -c "from e2e_agent.skills.loader import SkillPackageLoader; print('skills:', SkillPackageLoader().list_skills())"
	$(PYTHON) -c "from e2e_agent.llm.wrapper import LLMWrapper; LLMWrapper(); print('llm wrapper OK')"
	$(PYTHON) -c "from e2e_agent.domains import DomainPackLoader; print('domains:', DomainPackLoader().list_domain_ids())"

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	rm -f e2e_agent.db
