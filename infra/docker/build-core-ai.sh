#!/bin/sh
# Builds the backend container image once so compose can reuse it.
docker build -t personal-ai-secretary-core-ai:latest ../../services/core-ai
