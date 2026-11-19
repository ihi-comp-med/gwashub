"""The pieces that run on the shared host (devtools/shared_host.py, infra/shared_host/gwashub-run) and the checks around them.

The example configuration is made up; the real one lives in infra/gwashub_infra/config.py (SHARED_HOSTS).
"""
import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from devtools import shared_host as sh

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "infra" / "shared_host" / "gwashub-run"

CFG = sh.SharedHostConfig(
    name="example", account="111111111111", region="us-east-1", site_url="https://gwashub.example.org",
    vpc_id="vpc-0abc", availability_zones=("us-east-1a", "us-east-1b"),
    batch_subnet_cidrs=("10.0.200.0/24", "10.0.201.0/24"), internet_gateway_id="igw-0abc",
    host_instance_id="i-0123456789abcdef0", host_role_name="dr-api-host", host_security_group_id="sg-0host",
    host_private_ip="10.0.0.83", nginx_cidr="10.0.0.12/32", api_port=8410, db_host="db.example.internal",
    banner="Test banner")

OUTPUTS = {
    "OutSiteUrl": "https://gwashub.example.org", "OutUploadsBucket": "uploads-b", "OutResultsBucket": "results-b",
    "OutResourcesBucket": "resources-b", "OutJobQueue": "arn:aws:batch:us-east-1:111111111111:job-queue/q",
    "OutImagesEnv": '{"gwashub/base": {"uri": "u@sha256:0", "job_definition": "arn:jd"}}',
    "OutEventsQueueUrl": "https://sqs.us-east-1.amazonaws.com/111111111111/events",
    "OutCognitoDomain": "https://gwashub-example.auth.us-east-1.amazoncognito.com", "OutUserPoolId": "us-east-1_ABC",
    "OutUserPoolClientId": "client123", "OutSessionSecret": "gwashub/example/session",
    "OutCognitoClientSecret": "gwashub/example/cognito-client", "OutDbSecret": "gwashub/example/db",
    "OutLogGroup": "/gwashub/example",
}

SECRETS = {"session": "s" * 64, "cognito_client": "cognito-secret",
           "db": {"username": "gwashub", "password": "p@ss/w:rd", "host": "db.example.internal", "port": 3306,
                  "dbname": "gwashub"}}

ENV_KEYS = {
    "GWASHUB_STORAGE", "GWASHUB_JOBS", "AWS_REGION", "GWASHUB_UPLOADS_BUCKET", "GWASHUB_RESULTS_BUCKET",
    "GWASHUB_RESOURCES_BUCKET", "GWASHUB_BATCH_QUEUE", "GWASHUB_IMAGES", "GWASHUB_EVENTS_QUEUE_URL",
    "GWASHUB_COGNITO_DOMAIN", "GWASHUB_COGNITO_USER_POOL_ID", "GWASHUB_COGNITO_CLIENT_ID",
    "GWASHUB_COGNITO_CLIENT_SECRET", "GWASHUB_PUBLIC_URL", "GWASHUB_CORS", "GWASHUB_SECRET", "GWASHUB_BANNER",
    "GWASHUB_DB_URL", "GWASHUB_WEB_DIR", "GWASHUB_DATA_DIR", "MPLCONFIGDIR", "HOME",
}


# ---------- the env file ----------

def test_env_values_complete_and_without_auth():
    values = sh.env_values(CFG, OUTPUTS, SECRETS)
    assert set(values) == ENV_KEYS
    assert "GWASHUB_AUTH" not in values
    assert all(isinstance(v, str) for v in values.values())
    assert values["GWASHUB_STORAGE"] == "s3" and values["GWASHUB_JOBS"] == "batch"
    assert values["AWS_REGION"] == "us-east-1"
    assert values["GWASHUB_BATCH_QUEUE"] == OUTPUTS["OutJobQueue"] and values["GWASHUB_IMAGES"] == OUTPUTS["OutImagesEnv"]
    assert values["GWASHUB_COGNITO_CLIENT_ID"] == "client123" and values["GWASHUB_COGNITO_CLIENT_SECRET"] == "cognito-secret"
    assert values["GWASHUB_PUBLIC_URL"] == values["GWASHUB_CORS"] == "https://gwashub.example.org"
    assert values["GWASHUB_SECRET"] == "s" * 64 and values["GWASHUB_BANNER"] == "Test banner"
    assert values["GWASHUB_WEB_DIR"] == "/opt/gwashub/current/web"
    assert values["GWASHUB_DATA_DIR"] == "/opt/gwashub/data"
    assert values["MPLCONFIGDIR"] == "/opt/gwashub/data/mpl" and values["HOME"] == "/opt/gwashub/data/home"


def test_db_url_escapes_credentials():
    url = sh.env_values(CFG, OUTPUTS, SECRETS)["GWASHUB_DB_URL"]
    assert url == ("mysql+pymysql://gwashub:p%40ss%2Fw%3Ard@db.example.internal:3306/gwashub"
                   "?charset=utf8mb4&ssl_ca=/opt/gwashub/rds-global-bundle.pem")


def test_env_values_name_missing_stack_outputs():
    with pytest.raises(ValueError, match="OutUserPoolId"):
        sh.env_values(CFG, {k: v for k, v in OUTPUTS.items() if k != "OutUserPoolId"}, SECRETS)


def test_render_env_quotes_shell_characters():
    assert sh.render_env({"X": "a b $HOME `id`"}) == "X='a b $HOME `id`'\n"
    assert sh.render_env({"B": "2", "A": "1"}) == "A='1'\nB='2'\n"  # sorted


def test_rendered_env_survives_the_shell():
    """What render_env writes is what a shell reads back, byte for byte, with nothing run."""
    nasty = {"A": "a b $HOME `id` $(id) \"q\" \\ ; & | * ? # !", "B": "tab\there", "C": ""}
    script = "set -a; . /dev/stdin; printf '%s\\n' \"$A\" \"$B\" \"[$C]\""
    out = subprocess.run(["bash", "-c", script], input=sh.render_env(nasty), capture_output=True, text=True, check=True)
    assert out.stdout == f"{nasty['A']}\n{nasty['B']}\n[]\n"


@pytest.mark.parametrize("value", ["it's", "a\nb", "a\x00b"])
def test_render_env_refuses_quote_and_newline(value):
    with pytest.raises(ValueError, match="SECRET_THING"):
        sh.render_env({"SECRET_THING": value})


def test_render_env_error_does_not_leak_the_value():
    with pytest.raises(ValueError) as e:
        sh.render_env({"K": "hunter2'"})
    assert "hunter2" not in str(e.value)


def test_render_env_refuses_auth():
    with pytest.raises(ValueError, match="GWASHUB_AUTH"):
        sh.render_env({"GWASHUB_AUTH": "dev"})


@pytest.mark.parametrize("key", ["A B", "A=B", "1A", "A;id", "", "A$B"])
def test_render_env_refuses_keys_that_are_not_shell_names(key):
    with pytest.raises(ValueError):
        sh.render_env({key: "x"})


def test_fetch_secrets_reads_the_three_named_secrets():
    asked = []

    class Fake:
        def get_secret_value(self, SecretId):
            asked.append(SecretId)
            return {"SecretString": {"gwashub/example/session": "sess", "gwashub/example/cognito-client": "cog",
                                     "gwashub/example/db": json.dumps(SECRETS["db"])}[SecretId]}

    got = sh.fetch_secrets(CFG, OUTPUTS, Fake())
    assert got == {"session": "sess", "cognito_client": "cog", "db": SECRETS["db"]}
    assert sorted(asked) == sorted(["gwashub/example/session", "gwashub/example/cognito-client", "gwashub/example/db"])


def test_stack_outputs_reads_the_shared_stack(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "INFRA", tmp_path)
    (tmp_path / "cdk-outputs-example.json").write_text(json.dumps({"Gwashub-example-Shared": OUTPUTS, "Other": {}}))
    assert sh.stack_outputs("example") == OUTPUTS
    (tmp_path / "cdk-outputs-three.json").write_text(json.dumps({"Gwashub-three-App": {}}))
    with pytest.raises(SystemExit, match="Gwashub-three-Shared"):
        sh.stack_outputs("three")
    with pytest.raises(SystemExit, match="cdk-outputs-nope.json"):
        sh.stack_outputs("nope")


def test_load_config(monkeypatch):
    monkeypatch.setitem(sh.SHARED_HOSTS, "example", CFG)
    assert sh.load_config("example") is CFG
    with pytest.raises(SystemExit, match="example"):  # names the known environments
        sh.load_config("nope")


# ---------- the wrapper ----------

def make_release(tmp_path: Path) -> Path:
    """A release folder: the wrapper where it sits in a real one, and a .venv/bin/python that is this interpreter."""
    release = tmp_path / "rel"
    (release / "infra" / "shared_host").mkdir(parents=True)
    shutil.copy2(WRAPPER, release / "infra" / "shared_host" / "gwashub-run")
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / ".venv" / "bin" / "python").symlink_to(sys.executable)
    return release


def run_wrapper(release: Path, env_file: Path, *args: str, **env) -> subprocess.CompletedProcess:
    full_env = {k: v for k, v in os.environ.items() if k not in ("GWASHUB_AUTH", "PYTHONPATH")}
    full_env.update({"GWASHUB_ENV_FILE": str(env_file), **env})
    return subprocess.run([str(release / "infra" / "shared_host" / "gwashub-run"), *args], capture_output=True, text=True,
                          env=full_env)


def test_wrapper_forces_cognito(tmp_path):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text(sh.render_env({"FOO": "a b $HOME"}) + "GWASHUB_AUTH='dev'\n")
    code = "import os;print(os.environ['GWASHUB_AUTH'], os.environ['FOO'], os.getcwd())"
    out = run_wrapper(release, env_file, "python", "-c", code)
    assert out.returncode == 0, out.stderr
    assert out.stdout == f"cognito a b $HOME {release.resolve()}\n"


def test_wrapper_overrides_auth_from_the_calling_environment(tmp_path):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text(sh.render_env({"FOO": "x"}))
    out = run_wrapper(release, env_file, "python", "-c", "import os;print(os.environ['GWASHUB_AUTH'])", GWASHUB_AUTH="dev")
    assert out.returncode == 0, out.stderr
    assert out.stdout == "cognito\n"


def test_wrapper_sets_pythonpath_to_the_release_and_its_api(tmp_path):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text("")
    out = run_wrapper(release, env_file, "python", "-c", "import os;print(os.environ['PYTHONPATH'])", PYTHONPATH="/elsewhere")
    real = release.resolve()
    assert out.stdout == f"{real}:{real}/api\n"


def test_wrapper_passes_arguments_through_untouched(tmp_path):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text("")
    args = ["a b", "*", "$HOME", "", "it's", "x\ny"]
    out = run_wrapper(release, env_file, "python", "-c", "import sys, json;print(json.dumps(sys.argv[1:]))", *args)
    assert json.loads(out.stdout) == args


@pytest.mark.parametrize("content", [None, "this is ( not valid shell\nGWASHUB_AUTH='dev'\n"])
def test_wrapper_does_not_run_anything_when_the_env_file_is_missing_or_broken(tmp_path, content):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    if content is not None:
        env_file.write_text(content)
    marker = tmp_path / "ran"
    out = run_wrapper(release, env_file, "python", "-c", f"open({str(marker)!r}, 'w').close()")
    assert out.returncode != 0
    assert not marker.exists()


def test_wrapper_needs_a_command(tmp_path):
    release = make_release(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text("")
    assert run_wrapper(release, env_file).returncode != 0


def test_wrapper_is_executable_in_git():
    assert os.access(WRAPPER, os.X_OK)
    staged = subprocess.run(["git", "ls-files", "-s", str(WRAPPER)], capture_output=True, text=True, cwd=ROOT).stdout
    assert not staged or staged.startswith("100755"), staged  # once committed, it must carry the executable bit


# ---------- unit files and nginx ----------

def test_render_unit_api():
    unit = sh.render_unit("api", CFG)
    assert ("ExecStart=/opt/gwashub/current/infra/shared_host/gwashub-run uvicorn gwashub_api.main:app --app-dir api "
            "--host 10.0.0.83 --port 8410 --proxy-headers --forwarded-allow-ips 10.0.0.12\n") in unit
    for line in ("Description=GWASHub API", "User=gwashub", "Group=gwashub", "MemoryMax=1536M", "MemoryLimit=1536M",
                 "CPUQuota=100%", "Restart=always", "RestartSec=5", "NoNewPrivileges=yes", "PrivateTmp=yes",
                 "ProtectHome=yes", "ProtectSystem=full", "After=network-online.target", "Wants=network-online.target",
                 "WantedBy=multi-user.target"):
        assert line + "\n" in unit, line
    assert unit.index("[Unit]") < unit.index("[Service]") < unit.index("[Install]")


def test_render_unit_worker():
    unit = sh.render_unit("worker", CFG)
    assert unit.count("ExecStart=") == 1
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert exec_start == "ExecStart=/opt/gwashub/current/infra/shared_host/gwashub-run python -m gwashub_api.worker"
    assert "Description=GWASHub worker (Batch job events)\n" in unit
    assert "MemoryMax=512M\n" in unit and "MemoryLimit=512M\n" in unit and "CPUQuota=50%\n" in unit
    assert "1536M" not in unit


def test_worker_unit_differs_from_api_unit_only_where_the_brief_says():
    api, worker = sh.render_unit("api", CFG).splitlines(), sh.render_unit("worker", CFG).splitlines()
    differing = [(a.split("=")[0]) for a, w in zip(api, worker) if a != w]
    assert len(api) == len(worker)
    assert differing == ["Description", "ExecStart", "MemoryMax", "MemoryLimit", "CPUQuota"]


def test_render_unit_needs_single_nginx_address():
    import dataclasses
    for cidr in ("10.0.0.0/24", "10.0.0.12", "10.0.0.12/31", "not-an-address/32"):
        with pytest.raises(ValueError, match="nginx_cidr"):
            sh.render_unit("api", dataclasses.replace(CFG, nginx_cidr=cidr))
    with pytest.raises(ValueError):
        sh.render_unit("web", CFG)


def test_render_nginx_overwrites_forwarding_headers():
    conf = sh.render_nginx(CFG, "/etc/nginx/ssl/site.crt", "/etc/nginx/ssl/site.key")
    for line in ("proxy_pass http://10.0.0.83:8410;", "proxy_set_header Host $host;",
                 "proxy_set_header X-Forwarded-Proto $scheme;", "proxy_set_header X-Forwarded-For $remote_addr;",
                 "proxy_read_timeout 120s;", "client_max_body_size 25m;", "listen 443 ssl;", "listen 80;",
                 "server_name gwashub.example.org;", "ssl_certificate /etc/nginx/ssl/site.crt;",
                 "ssl_certificate_key /etc/nginx/ssl/site.key;", "return 301 https://$host$request_uri;"):
        assert line in conf, line
    assert "$proxy_add_x_forwarded_for" not in conf
    assert conf.count("server {") == 2 and conf.count("location /") == 1


@pytest.mark.parametrize("bad", ["/etc/x;y", "/etc/x y", "/etc/x\n}", "/etc/$x", '/etc/"x'])
def test_render_nginx_refuses_paths_that_would_break_the_config(bad):
    with pytest.raises(ValueError):
        sh.render_nginx(CFG, bad, "/etc/nginx/ssl/site.key")
    with pytest.raises(ValueError):
        sh.render_nginx(CFG, "/etc/nginx/ssl/site.crt", bad)


# ---------- checks ----------

def site(health=(200, {"ok": True}), users=(404, {"detail": "Not Found"}), login=(405, {"detail": "Method Not Allowed"})):
    """A fake site answering the three requests check_site makes."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        answers = {("GET", "/api/health"): health, ("GET", "/api/auth/users"): users, ("POST", "/api/auth/login"): login}
        status, body = answers.get((request.method, request.url.path), (404, {}))
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_check_site_passes_cognito_site():
    client, seen = site()
    assert sh.check_site("https://gwashub.example.org", client) == []
    assert ("POST", "/api/auth/login") in seen


def test_check_site_accepts_404_from_login_too():
    client, _ = site(login=(404, {"detail": "Not Found"}))
    assert sh.check_site("https://gwashub.example.org/", client) == []


def test_check_site_flags_dev_login():
    client, _ = site(login=(422, {"detail": "bad body"}))
    problems = sh.check_site("https://gwashub.example.org", client)
    assert len(problems) == 1 and "/api/auth/login" in problems[0]


def test_check_site_flags_dev_user_list():
    client, _ = site(users=(200, []))
    problems = sh.check_site("https://gwashub.example.org", client)
    assert len(problems) == 1 and "/api/auth/users" in problems[0]


def test_check_site_flags_a_sick_site():
    for health in ((500, {"detail": "boom"}), (200, {"ok": False}), (200, {"hello": 1})):
        client, _ = site(health=health)
        problems = sh.check_site("https://gwashub.example.org", client)
        assert len(problems) == 1 and "/api/health" in problems[0]


def test_check_site_flags_a_non_json_health_page():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404 if "auth" in r.url.path else 200, text="<html>nginx welcome</html>")))
    problems = sh.check_site("https://gwashub.example.org", client)
    assert len(problems) == 1 and "/api/health" in problems[0]


def test_check_site_reports_an_unreachable_site():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    problems = sh.check_site("https://gwashub.example.org", httpx.Client(transport=httpx.MockTransport(refuse)))
    assert len(problems) == 3 and all("refused" in p for p in problems)


def test_check_site_does_not_follow_redirects():
    """A redirect to somewhere else must not make a site look fine."""
    def redirect(request):
        return httpx.Response(301, headers={"location": "https://elsewhere.example.org/"})
    client = httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True)
    assert sh.check_site("http://gwashub.example.org", client)


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    """The real API on a throwaway SQLite file, with no AWS calls: returns a function that starts it in a sign-in mode."""
    from fastapi.testclient import TestClient
    from gwashub_api import config, db
    for k in ("GWASHUB_DB_URL", "GWASHUB_DB_HOST", "GWASHUB_STORAGE", "GWASHUB_JOBS", "GWASHUB_WEB_DIR", "GWASHUB_AUTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GWASHUB_DATA_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("GWASHUB_PRACTICE", "0")
    running = ExitStack()

    def start(mode: str) -> TestClient:
        monkeypatch.setenv("GWASHUB_AUTH", mode)
        if mode == "cognito":
            for k, v in {"GWASHUB_SECRET": "x" * 40, "GWASHUB_COGNITO_DOMAIN": "https://x.auth.us-east-1.amazoncognito.com",
                         "GWASHUB_COGNITO_USER_POOL_ID": "us-east-1_X", "GWASHUB_COGNITO_CLIENT_ID": "c",
                         "GWASHUB_COGNITO_CLIENT_SECRET": "s"}.items():
                monkeypatch.setenv(k, v)
        config.settings.cache_clear()
        db.reset_engine()
        from gwashub_api.main import app
        client = running.enter_context(TestClient(app))  # its start-up creates the schema in the temp file
        assert config.settings().auth_mode == mode
        return client

    yield start
    running.close()
    db.reset_engine()
    config.settings.cache_clear()


def test_check_site_against_real_app_in_cognito_mode(real_app):
    client = real_app("cognito")
    assert sh.check_site("http://testserver", client) == []


def test_check_site_against_real_app_in_dev_mode(real_app):
    client = real_app("dev")
    problems = sh.check_site("http://testserver", client)
    assert any("/api/auth/users" in p for p in problems), problems


# ---------- deploy guards ----------

def test_preflight_refuses_dirty_tree_and_wrong_account():
    assert len(sh.preflight(CFG, "222222222222", " M api/x.py")) == 2
    assert sh.preflight(CFG, "111111111111", "") == []
    (wrong_account,) = sh.preflight(CFG, "222222222222", "")
    assert "222222222222" in wrong_account and "111111111111" in wrong_account
    (dirty,) = sh.preflight(CFG, "111111111111", " M api/x.py\n?? new.txt\n")
    assert "api/x.py" in dirty and "new.txt" in dirty


def test_preflight_refuses_when_the_account_is_unknown():
    assert sh.preflight(CFG, "", "")


def test_releases_to_remove():
    names = ["20261101-100000-aaaaaaa", "20261102-100000-bbbbbbb", "20261103-100000-ccccccc",
             "20261104-100000-ddddddd", "20261105-100000-eeeeeee"]
    # current = the 3rd newest (a rollback): it and the two newest others stay, the two oldest go
    assert sh.releases_to_remove(names, "20261103-100000-ccccccc") == names[:2]
    # normal deploy: current is the newest; it and the 2 newest others stay
    assert sh.releases_to_remove(names, names[4]) == names[:2]
    assert sh.releases_to_remove(names, names[4], keep=1) == names[:3]
    assert sh.releases_to_remove(list(reversed(names)), names[4]) == names[:2]  # order given doesn't matter
    assert sh.releases_to_remove(names[:3], names[2]) == []
    # a folder that isn't a release is never removed
    assert sh.releases_to_remove(names + ["notes", "lost+found"], names[4]) == names[:2]


def test_limits_in_force():
    assert sh.limits_in_force("MemoryMax=infinity\nMemoryLimit=1610612736\n") is True
    assert sh.limits_in_force("MemoryMax=infinity\nMemoryLimit=infinity\n") is False
    assert sh.limits_in_force("MemoryMax=1610612736\nMemoryLimit=infinity\n") is True
    assert sh.limits_in_force("MemoryMax=\nMemoryLimit=\n") is False
    assert sh.limits_in_force("MemoryMax=18446744073709551615\nMemoryLimit=18446744073709551615\n") is False
    assert sh.limits_in_force("") is False


def test_limits_in_force_needs_every_unit_limited():
    """`systemctl show` of two units prints one block each: both must have a limit."""
    limited = "MemoryMax=1610612736\nMemoryLimit=1610612736\n"
    unlimited = "MemoryMax=infinity\nMemoryLimit=infinity\n"
    assert sh.limits_in_force(limited + "\n" + limited) is True
    assert sh.limits_in_force(limited + "\n" + unlimited) is False
    assert sh.limits_in_force(unlimited + "\n" + limited) is False


# ---------- the command line ----------

def cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "devtools" / "shared_host.py"), *args], capture_output=True, text=True,
                          input=stdin, cwd=ROOT)


def test_cli_rejects_an_unknown_environment():
    out = cli("render-unit", "nope", "api")
    assert out.returncode != 0 and "nope" in out.stderr and out.stdout == ""


def test_cli_limits_ok_reads_stdin():
    assert cli("limits-ok", "example", stdin="MemoryMax=1610612736\nMemoryLimit=infinity\n").returncode == 0
    out = cli("limits-ok", "example", stdin="MemoryMax=infinity\nMemoryLimit=infinity\n")
    assert out.returncode != 0 and "Memory" in out.stderr


def test_cli_prune_prints_names_to_delete():
    names = ["20261101-100000-aaaaaaa", "20261102-100000-bbbbbbb", "20261103-100000-ccccccc", "20261104-100000-ddddddd"]
    out = cli("prune", "example", names[3], *names)
    assert out.returncode == 0 and out.stdout.split() == names[:1]


def test_cli_commands_for_an_environment(monkeypatch, capsys, tmp_path):
    """render-unit, render-nginx and render-env through main(), with the environment and AWS stubbed."""
    monkeypatch.setitem(sh.SHARED_HOSTS, "example", CFG)
    monkeypatch.setattr(sh, "stack_outputs", lambda env: OUTPUTS)
    monkeypatch.setattr(sh, "fetch_secrets", lambda cfg, outputs, client=None: SECRETS)
    assert sh.main(["render-unit", "example", "worker"]) == 0
    assert "gwashub_api.worker" in capsys.readouterr().out
    assert sh.main(["render-nginx", "example", "--cert", "/c.crt", "--key", "/c.key"]) == 0
    assert "server_name gwashub.example.org;" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    assert sh.main(["render-env", "example"]) == 0
    out = capsys.readouterr().out
    assert out == sh.render_env(sh.env_values(CFG, OUTPUTS, SECRETS))
    assert "GWASHUB_AUTH" not in out and list(tmp_path.iterdir()) == []  # nothing written to disk


def test_cli_check_site_and_preflight_exit_codes(monkeypatch, capsys):
    monkeypatch.setitem(sh.SHARED_HOSTS, "example", CFG)
    monkeypatch.setattr(sh, "check_site", lambda url, client=None: [])
    assert sh.main(["check-site", "example"]) == 0
    monkeypatch.setattr(sh, "check_site", lambda url, client=None: ["/api/auth/users answered 200"])
    assert sh.main(["check-site", "example"]) != 0
    assert "/api/auth/users answered 200" in capsys.readouterr().err
    monkeypatch.setattr(sh, "caller_account", lambda region: "222222222222")
    monkeypatch.setattr(sh, "git_status", lambda: " M api/x.py")
    assert sh.main(["preflight", "example"]) != 0
    assert "222222222222" in capsys.readouterr().err
    monkeypatch.setattr(sh, "caller_account", lambda region: "111111111111")
    monkeypatch.setattr(sh, "git_status", lambda: "")
    assert sh.main(["preflight", "example"]) == 0
