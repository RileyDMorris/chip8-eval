# Agent sandbox: the model works in /workspace as a non-root user.
# Started with --network none and CPU/memory limits; only tasks/<variant>/ is copied in.
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 agent \
 && mkdir -p /workspace \
 && chown agent:agent /workspace
ENV PYTHONDONTWRITEBYTECODE=1
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
