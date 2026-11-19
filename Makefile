.PHONY: install data run test lint serve all
install:  ## create .venv and install
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
data:     ## download the public data into data/raw (~75 MB)
	.venv/bin/python -m chargeplan download
run:      ## full analysis -> output/
	.venv/bin/python -m chargeplan run
test:
	.venv/bin/pytest --cov=chargeplan
lint:
	.venv/bin/ruff check src tests
serve:    ## preview the dashboard on http://localhost:8000
	python3 -m http.server 8000 -d output/site
all: data run test
