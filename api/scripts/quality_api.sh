#!/bin/sh
set -eu

cd /app

pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python
ruff check .
