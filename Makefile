UV ?= uv
RUN = $(UV) run --project plugin/server

.PHONY: test itest lint validate bench

test:  ## unit tests (no FreeCAD needed)
	$(RUN) pytest tests/unit -q

itest:  ## GUI integration tests against a running FreeCAD with ClaudeBridge
	$(RUN) pytest tests/gui -q

bench:  ## design benchmark; MODELS=opus,sonnet,haiku TASKS=a,b (default: all)
	cd tests && $(UV) run --project ../plugin/server python -m design.run --models $(or $(MODELS),opus) \
		$(if $(TASKS),--tasks $(TASKS))

lint:
	$(RUN) ruff check plugin tests spikes

validate:
	claude plugin validate .
