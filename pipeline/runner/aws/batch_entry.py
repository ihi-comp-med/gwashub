"""AWS Batch entrypoint: run the job described by a spec in S3.

    python -m pipeline.runner.aws.batch_entry s3://<results-bucket>/qc/<run-id>/spec.json [--group N]

A QC run is one Batch job per group of consecutive steps that share an image (--group N), submitted
one at a time by the worker from each job's hand-over (next_group); a meta-analysis is a single job. Outputs go to
the spec's output prefix, and the job that ends the run writes result.json. The job exits 0 whenever it recorded an outcome (including a
QC fail); a non-zero exit means an infrastructure error, which the worker records from Batch.

SIGTERM (Batch stopping the job, e.g. its Spot machine being reclaimed) stops the job at once without recording
anything, so Batch's retry runs the group again from the previous group's hand-over.
"""
import argparse
import json
import signal
import tempfile

from pipeline.runner import jobspec
from pipeline.runner.executor import Interrupted


def _stop(signum, frame):
    raise Interrupted("the job was told to stop (SIGTERM)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pipeline.runner.aws.batch_entry")
    ap.add_argument("spec", help="s3://bucket/key/spec.json")
    ap.add_argument("--group", type=int, default=None, help="QC only: run just this group of steps")
    args = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, _stop)
    if not args.spec.startswith("s3://"):
        ap.error("the spec must be an s3:// location")
    bucket, key = jobspec._split(args.spec)
    spec = json.loads(jobspec._s3().get_object(Bucket=bucket, Key=key)["Body"].read())
    what = f"{spec['kind']} job" + (f", group {args.group}" if args.group is not None else "")
    print(f"Running {what} → {spec['output']}", flush=True)
    out = jobspec.run_spec(spec, tempfile.mkdtemp(prefix="gwashub-job-"), group=args.group)
    print(f"Finished: {out.get('status')} {out.get('overall_verdict', '')}", flush=True)


if __name__ == "__main__":
    main()
