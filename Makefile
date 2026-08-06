# Macropol-s — SLPIE and Gratimos
#
#     make setup            install everything, from nothing
#     make lab              open the notebooks
#     make test             the suite (2404 tests, no network)
#     make notebooks-run    execute every notebook, fail on any error
#
# `make help` lists the rest.

PYTHON ?= python
.DEFAULT_GOAL := help

.PHONY: help
help:  ## show this list
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-20s\033[0m %s\n", $$1, $$2}'

# --- setup ----------------------------------------------------------------

.PHONY: setup
setup:  ## install the kernel, the notebook layer, and the Jupyter kernel
	bash .devcontainer/setup.sh

.PHONY: install
install:  ## install the kernel only — no third-party packages at all
	$(PYTHON) -m pip install -e .

.PHONY: install-dev
install-dev:  ## install with the development extras
	$(PYTHON) -m pip install -e '.[dev]'

# --- the notebooks --------------------------------------------------------

.PHONY: lab
lab: ## open JupyterLab on the notebooks
	$(PYTHON) -m jupyterlab notebooks/

.PHONY: notebooks
notebooks:  ## regenerate the notebooks from tools/notebooks/spec.py
	$(PYTHON) -m tools.notebooks.build

.PHONY: notebooks-check
notebooks-check:  ## fail if any notebook is stale against its spec
	$(PYTHON) -m tools.notebooks.build --check

.PHONY: notebooks-run
notebooks-run:  ## execute every notebook; any cell that raises fails the build
	$(PYTHON) -m tools.notebooks.run

# --- the suite ------------------------------------------------------------

.PHONY: test
test:  ## run the test suite
	$(PYTHON) -m pytest -q

.PHONY: test-fast
test-fast:  ## the suite without the slow tests
	$(PYTHON) -m pytest -q -m "not slow and not corpus"

.PHONY: coverage
coverage:  ## the suite, with a coverage report
	$(PYTHON) -m coverage run -m pytest -q
	$(PYTHON) -m coverage report

.PHONY: invariants
invariants:  ## the architectural boundaries, on their own
	$(PYTHON) -m pytest -q tests/test_slpie_boundaries.py tests/test_reuse_boundaries.py \
	    tests/test_slpie_audit.py tests/test_slpie_dispatch.py \
	    tests/test_enterprise_boundaries.py

.PHONY: lint
lint:  ## check import order (no black — see the note in pyproject.toml)
	$(PYTHON) -m isort --check-only --diff slpie slpie_enterprise gratimos tests tools

.PHONY: format
format:  ## fix import order
	$(PYTHON) -m isort slpie slpie_enterprise gratimos tests tools

# --- using it -------------------------------------------------------------

.PHONY: demo
demo:  ## the narrated end-to-end run
	$(PYTHON) -m slpie.cli demo

.PHONY: manual
manual:  ## regenerate docs/MANUAL.md from the verb registry
	$(PYTHON) -m slpie.cli manual > docs/MANUAL.md
	@echo "wrote docs/MANUAL.md"

.PHONY: audit
audit:  ## judge this repository against its own stated architecture
	$(PYTHON) -m slpie.cli audit

.PHONY: ui
ui:  ## serve the stdlib interface on :8765
	$(PYTHON) -c "from slpie.ui import UiServer; UiServer(engine=None, port=8765).serve_forever()"

.PHONY: clean
clean:  ## remove caches and build artifacts
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete
	rm -rf .pytest_cache .coverage htmlcov build dist *.egg-info
