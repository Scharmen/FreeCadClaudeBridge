UV ?= uv
RUN = $(UV) run --project plugin/server

.PHONY: test itest lint validate

test:  ## unit tests (no FreeCAD needed)
	$(RUN) pytest tests/unit -q

itest:  ## GUI integration tests against a running FreeCAD with ClaudeBridge
	$(RUN) pytest tests/gui -q

lint:
	$(RUN) ruff check plugin tests spikes

validate:
	claude plugin validate .
