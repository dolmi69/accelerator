# Trusted stdlib worker is mounted read-only by hosting_runtime, together with
# this release's sources. No platform code, environment, secrets or Docker socket.
FROM public.ecr.aws/docker/library/python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
