export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

RUN := docker compose run --rm watcher

.PHONY: check build test lint format init login pick-chats run listen clear-halt

check: lint test

build:
	docker compose build

test:
	$(RUN) npm test

lint:
	$(RUN) npm run lint

format:
	$(RUN) npm run format

init login pick-chats run listen clear-halt:
	$(RUN) telegram-watcher $@
