LAB ?=
SUT ?=
# Labs with no LLM skip the models-check one-shot (spec 06 §5).
NO_LLM_LABS := lab00 lab08
ifneq ($(filter $(LAB),$(NO_LLM_LABS)),)
export SKIP_MODEL_CHECK := true
endif
PROFILES := $(if $(filter container,$(OLLAMA_MODE)),--profile ollama,)

.PHONY: setup models up down logs notebook test e2e tck lint coverage-tags

setup:
	uv sync --all-packages --all-extras
	-uv run pre-commit install

models:
	uv run scripts/check_models.py

up:
	$(if $(filter lab06 all,$(LAB)),uv run scripts/gen_lab06_keys.py)
	docker compose $(PROFILES) --profile $(LAB) up -d --build

down:
	docker compose --profile "*" down

logs:
	docker compose --profile $(LAB) logs -f

notebook:
	docker compose up -d jupyter
	@echo "http://localhost:8888/lab?token=$${JUPYTER_TOKEN:-a2a-labs}"

test:
	uv run pytest -m "not compose and not llm and not slow" libs services labs

e2e:
	$(MAKE) up LAB=$(LAB)
	uv run scripts/wait_for.py --lab $(LAB)
	uv run pytest -m compose labs/$(LAB)-*/tests
	uv run pytest --nbmake labs/$(LAB)-*/notebooks/*.ipynb

tck:
	./scripts/run_tck.sh $(SUT)

lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy libs

coverage-tags:
	uv run scripts/check_coverage_tags.py
