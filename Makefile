export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

RUN := docker compose run --rm watcher

.PHONY: check build test lint format init login pick-chats run listen clear-halt

check: lint test

build:
	docker compose build

test:
	$(RUN) pytest -q -W error

lint:
	$(RUN) sh -c "ruff check && ruff format --check"

format:
	$(RUN) sh -c "ruff check --fix && ruff format"

init login pick-chats run listen clear-halt:
	$(RUN) telegram-watcher $@
