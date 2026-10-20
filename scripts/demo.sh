#!/usr/bin/env bash

COMMAND=$1

case "$COMMAND" in
  infra-up)
    echo "🏗️ Booting baseline infrastructure from local fixture..."
    docker compose -f tests/fixtures/docker-compose.test.yaml up -d
    echo "✅ Infrastructure running. (Note: DriftWarden validates against GitHub, not this local file)."
    ;;
    
  drift)
    echo "🚨 Injecting chaos into the host environment..."
    echo "1. Spawning a ROGUE container (unauthorized database, no compose labels)..."
    docker run -d --name rogue-db -p 3306:3306 -e MARIADB_ROOT_PASSWORD=rogue mariadb:latest
    
    echo "2. Deleting a required container to trigger MISSING state (web)..."
    docker rm -f infrastructure-test-web-1 || true
    
    echo "3. Mutating a container to trigger DRIFTED state (spoofing the compose project)..."
    docker rm -f infrastructure-test-cache-1 || true
    docker run -d --name infrastructure-test-cache-1 -p 6378:6378 \
        --label com.docker.compose.project=infrastructure-test \
        --label com.docker.compose.service=cache \
        --label warden.managed=true \
        --label warden.repo=my-cs-project \
        redis:latest
        
    echo "✅ Chaos injected. Watch the DriftWarden dashboard react!"
    ;;
    
  clean)
    echo "🧹 Wiping demo containers and resetting host..."
    docker compose -f tests/fixtures/docker-compose.test.yaml down
    docker rm -f rogue-db infrastructure-test-cache-1 infrastructure-test-web-1 2>/dev/null || true
    docker network rm infrastructure-test_default 2>/dev/null || true
    echo "✅ Environment completely reset."
    ;;
    
  *)
    echo "Usage: bash scripts/demo.sh {infra-up|drift|clean}"
    exit 1
    ;;
esac