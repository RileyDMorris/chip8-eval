"""Check everything a real eval needs, cheaply, before spending on full runs.

    python -m harness.preflight                       # all non-smoke models in models.yaml
    python -m harness.preflight --models gpt-6-astra --no-api

Checks: Docker is up, sandbox/grader images build, the agent sandbox has no
network, each model's API key is set, and (unless --no-api) one tiny real
request per model with the eval's actual tool definitions, which catches a
wrong model ID, a bad key, or a parameter the provider rejects. The API check
costs a few thousand input tokens per model.
"""

import argparse
import sys

from grader.run_candidate import docker_available
from harness.agent_loop import TOOLS
from harness.providers import ProviderError, make_client
from harness.run_eval import load_config, load_dotenv, select_models
from harness.sandbox import Sandbox, ensure_images

PING = "This is a connectivity check. Call the bash tool once with the command `echo ok`, and nothing else."


def check(label, fn):
    try:
        detail = fn()
        print(f"  ok    {label}{': ' + detail if detail else ''}")
        return True
    except Exception as e:  # report every failure, keep checking
        print(f"  FAIL  {label}: {e}")
        return False


def docker_up():
    if not docker_available():
        raise RuntimeError("not running")


def images_built():
    ensure_images()


def sandbox_offline():
    with Sandbox() as sandbox:
        _, output, _ = sandbox.bash("python3 -c \"import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)\" "
                                    "2>&1 | tail -1")
    if "unreachable" not in output.lower() and "error" not in output.lower():
        raise RuntimeError(f"agent sandbox reached the network: {output.strip()}")
    return "no network inside the agent sandbox"


def ping_model(model_cfg):
    client = make_client(model_cfg)
    client.start("You are being tested for connectivity.", PING, TOOLS)
    turn = client.step()
    names = [c.name for c in turn.tool_calls]
    usage = turn.usage
    note = f"tool calls {names or 'none'}, {usage.input_tokens} in / {usage.output_tokens} out tokens"
    if turn.refusal:
        raise RuntimeError("model refused the connectivity check")
    return note


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="harness/models.yaml")
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--no-api", action="store_true", help="skip the real API request")
    args = parser.parse_args(argv)
    load_dotenv()
    config = load_config(args.config)
    models = select_models(config, args.models)

    ok = True
    print("infrastructure")
    ok &= check("docker daemon", docker_up)
    if ok:
        ok &= check("agent + grader images", images_built)
        ok &= check("sandbox isolation", sandbox_offline)
    for model in models:
        print(f"model {model['name']} ({model['provider']}: {model.get('model', '-')})")
        if args.no_api:
            ok &= check("client config + API key", lambda m=model: make_client(m) and None)
        else:
            ok &= check("live request with eval tools", lambda m=model: ping_model(m))
    print("\nall checks passed" if ok else "\nsome checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
