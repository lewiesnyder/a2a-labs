LAB ?=
SUT ?=
PROFILES := $(if $(filter container,$(OLLAMA_MODE)),--profile ollama,)

.PHONY: setup models up down logs notebook test e2e tck lint

setup:
	uv sync --all-packages --all-extras
	-uv run pre-commit install

models:
	uv run scripts/check_models.py

up:
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
	uv run pytest -m compose labs/$(LAB)*
	uv run pytest --nbmake labs/$(LAB)*/notebooks

tck:
	./scripts/run_tck.sh $(SUT)

lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy libs
