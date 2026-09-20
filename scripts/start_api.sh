#!/usr/bin/env bash
# Build and serve the API locally. One place for the flags that matter:
#
#   --docker-network exodus_net  handler containers must share LocalStack's network
#   --warm-containers EAGER      without it every request cold-starts a container
#                                (~1.6s each; a few polling browsers overload it)
#   (no --host)                  binds 127.0.0.1 only. Identity is an unverified
#                                header, so don't expose this to the network.
#   EXODUS_TRAVEL_DATE=YYYY-MM-DD  the day of the trip. Without it, departures are
#                                measured against the day of each release, so a demo run
#                                after the pool's departure times has no room for a third
#                                rider. Set it to tomorrow to see one being found.
#
# Warm containers keep old code loaded: after editing a handler, re-run this script.

set -euo pipefail
cd "$(dirname "$0")/.."

sam build
exec sam local start-api --docker-network exodus_net --port 3000 --warm-containers EAGER \
  --parameter-overrides "TravelDate=${EXODUS_TRAVEL_DATE:-}"
