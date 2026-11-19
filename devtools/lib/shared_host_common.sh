# shellcheck shell=bash
# What devtools/shared_host_{setup,deploy,admin}.sh have in common. Source it from the repository root with ENV_NAME set
# (the environment's name in SHARED_HOSTS, infra/gwashub_infra/config.py); it defines functions and runs only local checks.
#
# The host is reached over SSH at $GWASHUB_SSH_TARGET (for example ec2-user@<host>). That address is never in git.

die() { echo "$*" >&2; exit 1; }

: "${GWASHUB_SSH_TARGET:?Set GWASHUB_SSH_TARGET to the SSH login for the shared host, for example ec2-user@<host>}"

# cfg <field>: a field of the environment's SharedHostConfig (region, account, host_private_ip, api_port, ...).
# An unknown environment stops the script with the list of known ones.
cfg() {
  .venv/bin/python -c 'import sys; from devtools.shared_host import load_config; print(getattr(load_config(sys.argv[1]), sys.argv[2]))' \
    "$ENV_NAME" "$1"
}

# The AWS account is the environment's own; the region always comes from its configuration, so a stale AWS_REGION in this
# shell cannot send images or stacks to the wrong place.
CONFIG_REGION=$(cfg region)
export AWS_PROFILE=${AWS_PROFILE:-gwashub-$ENV_NAME} AWS_REGION=$CONFIG_REGION AWS_DEFAULT_REGION=$CONFIG_REGION

# remote WORD...: run one command on the host, non-interactively. Every word is quoted for the remote shell (printf %q), so a
# value from this machine is never read as shell there. It starts in /opt/gwashub, a folder the gwashub user can read (the
# login folder is not, and `sudo -u gwashub` keeps it). Standard input is passed through: pipe data in, or use a here-document
# with `remote sudo bash -s -- ARG...` to run a script.
remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$GWASHUB_SSH_TARGET" "cd /opt/gwashub 2>/dev/null; $(printf '%q ' "$@")"
}
