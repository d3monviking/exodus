#!/usr/bin/env bash
# Build and serve the API locally. One place for the flags that matter:
#
#   --docker-network exodus_net  handler containers must share LocalStack's network
#   --warm-containers EAGER      without it every request cold-starts a container
#                                (~1.6s each; a few polling browsers overload it)
#   (no --host)                  binds 127.0.0.1 only. Identity is an unverified
#                                header, so don't expose this to the network.
#
# Warm containers keep old code loaded: after editing a handler, re-run this script.

set -euo pipefail
cd "$(dirname "$0")/.."

sam build
exec sam local start-api --docker-network exodus_net --port 3000 --warm-containers EAGER
