ARG HERMES_IMAGE=nousresearch/hermes-agent@sha256:cbcbe555961c3f36124db521a179b5b61eea00acf5629a27086c9350899df191
FROM ${HERMES_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv python-is-python3 \
    && rm -rf /var/lib/apt/lists/* \
    && python --version \
    && python3 --version \
    && pip3 --version

COPY --chmod=0444 container/policy.py /usr/local/lib/hermes_naked_policy.py
COPY --chmod=0444 container/policy.py /usr/local/lib/hermesctl_policy.py
COPY --chmod=0444 container/worker_policy.py /usr/local/lib/hermes_worker_policy.py
COPY --chmod=0555 container/hermes-naked.py /usr/local/bin/hermes-naked
COPY --chmod=0555 container/hermesctl-mcp.py /usr/local/bin/hermesctl-mcp
COPY --chmod=0444 container/worker_rpc.py /usr/local/lib/worker_rpc.py
COPY --chmod=0555 container/worker-mcp.py /usr/local/bin/worker-mcp
COPY --chmod=0555 container/agentctl.py /usr/local/bin/registered-agent
COPY --chmod=0555 container/agent-mcp.py /usr/local/bin/agent-mcp
COPY --chmod=0555 container/control_cli.py /usr/local/bin/hermes-control
COPY --chmod=0555 container/control_plane /usr/local/lib/control_plane
COPY --chmod=0555 hermesctl /usr/local/bin/hermesctl
