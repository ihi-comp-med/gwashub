"""The pieces of a shared-host deployment that run on the host, and the checks around a deploy.

GWASHub on an existing host runs as two systemd services (API and worker) behind the host's nginx proxy, using an existing
MySQL server (see infra/gwashub_infra/shared_host_stack.py). This module renders what goes on the host from the
environment's settings (infra/gwashub_infra/config.py, SHARED_HOSTS) and the stack's outputs; the deploy scripts
(devtools/shared_host_*.sh) call it. Every command takes the environment name first:

    python devtools/shared_host.py render-env <env>               # the env file, on stdout only: it holds secrets
    python devtools/shared_host.py render-unit <env> api|worker   # a systemd unit
    python devtools/shared_host.py render-nginx <env> --cert P --key P
    python devtools/shared_host.py check-site <env>               # health, and the development sign-in is not there
    python devtools/shared_host.py preflight <env>                # right AWS account, clean git tree
    python devtools/shared_host.py prune <env> <current> <releases...>   # which release folders to delete
    python devtools/shared_host.py limits-ok <env> < systemctl-show-output

A check that fails prints its problems on stderr and exits 1. Nothing here writes a secret to disk.
"""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote, urlparse

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA = os.path.join(ROOT, "infra")
if INFRA not in sys.path:
    sys.path.append(INFRA)  # gwashub_infra.config imports only the standard library, so no CDK is needed here

from gwashub_infra.config import SHARED_HOSTS, SharedHostConfig  # noqa: E402

PREFIX = "/opt/gwashub"
RUN = f"{PREFIX}/current/infra/shared_host/gwashub-run"
RDS_CA_BUNDLE = f"{PREFIX}/rds-global-bundle.pem"
RELEASE_NAME = re.compile(r"^\d{8}-\d{6}-[0-9a-f]+$")  # <UTC yyyymmdd-hhmmss>-<git short sha>
SHELL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NGINX_PATH = re.compile(r"^[^\s;{}\"'$#\\]+$")  # one nginx word: nothing that ends or changes a directive
UNLIMITED = ("", "infinity", "18446744073709551615")  # what `systemctl show` prints for "no limit"


def load_config(env: str) -> SharedHostConfig:
    if env not in SHARED_HOSTS:
        raise SystemExit(f"Unknown shared-host environment {env!r}. Known: {', '.join(SHARED_HOSTS) or 'none'}")
    return SHARED_HOSTS[env]


def stack_outputs(env: str) -> dict[str, str]:
    """The outputs of Gwashub-<env>-Shared, from the file `cdk deploy --outputs-file` wrote."""
    path = os.path.join(INFRA, f"cdk-outputs-{env}.json")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found: deploy the stack first (devtools/shared_host_deploy.sh {env})")
    with open(path) as f:
        data = json.load(f)
    stack = f"Gwashub-{env}-Shared"
    if stack not in data:
        raise SystemExit(f"{path} has no {stack} entry (it has: {', '.join(data) or 'nothing'})")
    return data[stack]


# ---------- the env file ----------

STACK_OUTPUTS_USED = ("OutUploadsBucket", "OutResultsBucket", "OutResourcesBucket", "OutJobQueue", "OutImagesEnv",
                      "OutEventsQueueUrl", "OutCognitoDomain", "OutUserPoolId", "OutUserPoolClientId")


def fetch_secrets(cfg: SharedHostConfig, outputs: dict[str, str], client=None) -> dict:
    """The three secrets the stack created, read with the caller's AWS credentials: {"session", "cognito_client", "db"}."""
    if client is None:
        import boto3
        client = boto3.client("secretsmanager", region_name=cfg.region)

    def read(output: str) -> str:
        name = outputs.get(output)
        if not name:
            raise SystemExit(f"The stack outputs have no {output}: deploy the stack first")
        try:
            return client.get_secret_value(SecretId=name)["SecretString"]
        except Exception as e:  # botocore's ClientError, expired credentials, no network, a binary secret
            raise SystemExit(f"Could not read secret {name}: {type(e).__name__}: {e}") from None

    return {"session": read("OutSessionSecret"), "cognito_client": read("OutCognitoClientSecret"),
            "db": json.loads(read("OutDbSecret"))}


def env_values(cfg: SharedHostConfig, outputs: dict[str, str], secrets: dict) -> dict[str, str]:
    """What the API and worker read (api/gwashub_api/config.py). Never GWASHUB_AUTH: gwashub-run sets that."""
    missing = [k for k in STACK_OUTPUTS_USED if k not in outputs]
    if missing:
        raise ValueError(f"The stack outputs lack {', '.join(missing)}: deploy the stack first")
    db = secrets["db"]
    missing = [k for k in ("username", "password", "host", "port", "dbname") if k not in db]
    if missing:
        raise ValueError(f"The database secret lacks {', '.join(missing)}")
    url = (f"mysql+pymysql://{quote(db['username'], safe='')}:{quote(db['password'], safe='')}"
           f"@{db['host']}:{db['port']}/{quote(db['dbname'], safe='')}?charset=utf8mb4&ssl_ca={RDS_CA_BUNDLE}")
    return {
        "GWASHUB_STORAGE": "s3", "GWASHUB_JOBS": "batch", "AWS_REGION": cfg.region,
        "GWASHUB_UPLOADS_BUCKET": outputs["OutUploadsBucket"], "GWASHUB_RESULTS_BUCKET": outputs["OutResultsBucket"],
        "GWASHUB_RESOURCES_BUCKET": outputs["OutResourcesBucket"],
        "GWASHUB_BATCH_QUEUE": outputs["OutJobQueue"], "GWASHUB_IMAGES": outputs["OutImagesEnv"],
        "GWASHUB_EVENTS_QUEUE_URL": outputs["OutEventsQueueUrl"],
        "GWASHUB_COGNITO_DOMAIN": outputs["OutCognitoDomain"], "GWASHUB_COGNITO_USER_POOL_ID": outputs["OutUserPoolId"],
        "GWASHUB_COGNITO_CLIENT_ID": outputs["OutUserPoolClientId"],
        "GWASHUB_COGNITO_CLIENT_SECRET": secrets["cognito_client"],
        "GWASHUB_PUBLIC_URL": cfg.site_url, "GWASHUB_CORS": cfg.site_url,
        "GWASHUB_SECRET": secrets["session"], "GWASHUB_BANNER": cfg.banner,
        "GWASHUB_DB_URL": url,
        "GWASHUB_WEB_DIR": f"{PREFIX}/current/web", "GWASHUB_DATA_DIR": f"{PREFIX}/data",
        "MPLCONFIGDIR": f"{PREFIX}/data/mpl", "HOME": f"{PREFIX}/data/home",
    }


def render_env(values: dict[str, str]) -> str:
    """One KEY='value' line per key, sorted: a file the shell can source and that runs nothing, whatever the values hold.

    Inside single quotes the shell expands nothing, so any value is safe except one holding a single quote; a newline or NUL
    would break the one-line-per-key format, so those are refused too. Errors name the key, never the value."""
    lines = []
    for key in sorted(values):
        if key == "GWASHUB_AUTH":
            raise ValueError("GWASHUB_AUTH must not be in the env file: gwashub-run sets it")
        if not SHELL_NAME.match(key):
            raise ValueError(f"{key!r} is not a valid environment variable name")
        value = values[key]
        for char, what in (("'", "a single quote"), ("\n", "a newline"), ("\x00", "a NUL character")):
            if char in value:
                raise ValueError(f"The value of {key} contains {what}, which the env file cannot hold")
        lines.append(f"{key}='{value}'")
    return "".join(line + "\n" for line in lines)


# ---------- systemd units and nginx ----------

UNIT = """[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
User=gwashub
Group=gwashub
ExecStart={exec_start}
Restart=always
RestartSec=5
MemoryMax={memory}
MemoryLimit={memory}
CPUQuota={cpu}
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
"""


def nginx_address(cfg: SharedHostConfig) -> str:
    """The proxy's single address: the only one whose X-Forwarded-* headers the API believes."""
    cidr = cfg.nginx_cidr
    try:
        address = ipaddress.IPv4Address(cidr.removesuffix("/32")) if cidr.endswith("/32") else None
    except ValueError:
        address = None
    if address is None:
        raise ValueError(f"nginx_cidr must be one IPv4 address ending in /32, not {cidr!r}")
    return str(address)


def render_unit(kind: str, cfg: SharedHostConfig) -> str:
    if kind == "api":
        exec_start = (f"{RUN} uvicorn gwashub_api.main:app --app-dir api --host {cfg.host_private_ip} --port {cfg.api_port} "
                      f"--proxy-headers --forwarded-allow-ips {nginx_address(cfg)}")
        return UNIT.format(description="GWASHub API", exec_start=exec_start, memory=cfg.api_memory, cpu=cfg.api_cpu)
    if kind == "worker":
        nginx_address(cfg)  # the same configuration rules for both units
        return UNIT.format(description="GWASHub worker (Batch job events)", exec_start=f"{RUN} python -m gwashub_api.worker",
                           memory=cfg.worker_memory, cpu=cfg.worker_cpu)
    raise ValueError(f"A unit is 'api' or 'worker', not {kind!r}")


NGINX = """server {{
    listen 443 ssl;
    server_name {host};

    ssl_certificate {cert};
    ssl_certificate_key {key};

    # GWASHub bulk upload manifests are up to 20 MB.
    client_max_body_size 25m;

    location / {{
        proxy_pass http://{ip}:{port};
        proxy_set_header Host $host;
        # Replaced, not appended to: GWASHub trusts these two headers from this proxy only.
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 120s;
    }}
}}

server {{
    listen 80;
    server_name {host};
    return 301 https://$host$request_uri;
}}
"""


def render_nginx(cfg: SharedHostConfig, cert_path: str, key_path: str) -> str:
    host = urlparse(cfg.site_url).hostname
    if not host:
        raise ValueError(f"site_url has no host name: {cfg.site_url!r}")
    for what, path in (("certificate", cert_path), ("key", key_path)):
        if not NGINX_PATH.match(path):
            raise ValueError(f"The {what} path {path!r} would break the nginx configuration")
    return NGINX.format(host=host, cert=cert_path, key=key_path, ip=cfg.host_private_ip, port=cfg.api_port)


# ---------- checks ----------

def check_site(base_url: str, client: httpx.Client | None = None) -> list[str]:
    """The problems with the site at base_url: it must be healthy, and the development sign-in must not be there.

    The site must say so itself: GET /api/auth/mode answers {"mode": "cognito", ...} (the real app: api/gwashub_api/main.py).
    And the development sign-in's endpoints must not answer: in a cognito-mode API /api/auth/users and POST /api/auth/login
    answer 404. The dev-mode list of users answers 200, so that is the decisive one; the login check is a second line (in
    dev mode it answers 200, or 404 for an unknown address, or 422 for a bad body)."""
    base = base_url.rstrip("/")
    own = client is None
    client = client or httpx.Client(timeout=20)
    problems = []

    def ask(method: str, path: str, **kw) -> httpx.Response | None:
        try:
            return client.request(method, base + path, follow_redirects=False, **kw)
        except httpx.HTTPError as e:
            problems.append(f"{path}: could not reach {base}: {type(e).__name__}: {e}")
            return None

    def json_object(r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    try:
        r = ask("GET", "/api/health")
        if r is not None and (r.status_code != 200 or json_object(r).get("ok") is not True):
            problems.append(f"/api/health answered {r.status_code}, expected 200 with {{\"ok\": true}}")
        r = ask("GET", "/api/auth/mode")
        if r is not None:
            mode = json_object(r).get("mode")
            if r.status_code != 200 or mode != "cognito":
                problems.append(f"/api/auth/mode answered {r.status_code} with mode {mode!r}, expected 200 with mode 'cognito': "
                                "the development sign-in may be on")
        r = ask("GET", "/api/auth/users")
        if r is not None and r.status_code != 404:
            problems.append(f"/api/auth/users answered {r.status_code}, expected 404: the development sign-in may be on")
        r = ask("POST", "/api/auth/login", json={"email": "check@example.org"})
        if r is not None and r.status_code not in (404, 405):
            problems.append(f"/api/auth/login answered {r.status_code}, expected 404 or 405: the development sign-in may be on")
    finally:
        if own:
            client.close()
    return problems


def preflight(cfg: SharedHostConfig, caller_account: str, git_status_porcelain: str) -> list[str]:
    """Refuse to deploy into the wrong AWS account or from a tree whose state the release tag would not describe."""
    problems = []
    if caller_account != cfg.account:
        problems.append(f"The AWS credentials are for account {caller_account or 'unknown'}, but {cfg.name} deploys to "
                        f"account {cfg.account} (check AWS_PROFILE)")
    if git_status_porcelain.strip():
        changed = "\n".join("  " + line for line in git_status_porcelain.rstrip().splitlines())
        problems.append(f"The git working tree isn't clean: commit, stash or ignore these first\n{changed}")
    return problems


def releases_to_remove(names: list[str], current: str, keep: int = 2) -> list[str]:
    """The release folders to delete, oldest first: keep `current` and the `keep` newest others.

    A name that isn't a release name (<UTC yyyymmdd-hhmmss>-<sha>) is never touched."""
    releases = sorted(n for n in set(names) if RELEASE_NAME.match(n))  # the timestamp comes first, so this is oldest first
    others = [n for n in releases if n != current]
    kept = set(others[-keep:] if keep > 0 else [])
    return [n for n in others if n not in kept]


def limits_in_force(systemctl_show: str) -> bool:
    """True if every unit in `systemctl show <units> -p MemoryMax -p MemoryLimit` has a memory limit set.

    Units are separated by a blank line; a unit counts if either property holds a value other than "no limit"."""
    blocks = [b for b in re.split(r"\n\s*\n", systemctl_show.strip()) if b.strip()]
    if not blocks:
        return False
    for block in blocks:
        values = [line.split("=", 1)[1].strip() for line in block.splitlines() if line.startswith(("MemoryMax=", "MemoryLimit="))]
        if not any(v not in UNLIMITED for v in values):
            return False
    return True


# ---------- command line ----------

def caller_account(region: str) -> str:
    out = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text", "--region", region],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def git_status() -> str:
    out = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(f"git status failed: {out.stderr.strip()}")
    return out.stdout


def fail(problems: list[str]) -> int:
    for p in problems:
        print(p, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def command(name: str):
        p = sub.add_parser(name)
        p.add_argument("env", help="environment name (a key of SHARED_HOSTS in infra/gwashub_infra/config.py)")
        return p

    command("render-env")
    command("render-unit").add_argument("kind", choices=("api", "worker"))
    nginx = command("render-nginx")
    nginx.add_argument("--cert", required=True, help="the certificate's path on the nginx machine")
    nginx.add_argument("--key", required=True, help="the private key's path on the nginx machine")
    command("check-site")
    command("preflight")
    prune = command("prune")
    prune.add_argument("current", help="the release now live: always kept")
    prune.add_argument("names", nargs="*", help="the folders in /opt/gwashub/releases")
    command("limits-ok")
    a = parser.parse_args(argv)

    try:
        if a.cmd == "prune":
            print("".join(n + "\n" for n in releases_to_remove(a.names, a.current)), end="")
            return 0
        if a.cmd == "limits-ok":
            if limits_in_force(sys.stdin.read()):
                return 0
            return fail(["A service has no MemoryMax or MemoryLimit in force: the memory limits are not being applied"])
        cfg = load_config(a.env)
        if a.cmd == "render-env":
            outputs = stack_outputs(a.env)
            sys.stdout.write(render_env(env_values(cfg, outputs, fetch_secrets(cfg, outputs))))
        elif a.cmd == "render-unit":
            sys.stdout.write(render_unit(a.kind, cfg))
        elif a.cmd == "render-nginx":
            sys.stdout.write(render_nginx(cfg, a.cert, a.key))
        elif a.cmd == "check-site":
            problems = check_site(cfg.site_url)
            if problems:
                return fail(problems)
            print(f"{cfg.site_url}: healthy, and the development sign-in is off")
        elif a.cmd == "preflight":
            problems = preflight(cfg, caller_account(cfg.region), git_status())
            if problems:
                return fail(problems)
            print(f"AWS account {cfg.account} and a clean git tree")
        return 0
    except ValueError as e:
        return fail([str(e)])


if __name__ == "__main__":
    sys.exit(main())
