#!/bin/sh
set -eu

cd /app

python /app/scripts/docker_pyright_config.py \
  < pyrightconfig.json \
  > pyrightconfig.docker.json

pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python
ruff check .
