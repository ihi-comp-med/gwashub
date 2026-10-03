"""Records AWS Batch job outcomes (GWASHUB_JOBS=batch).

    python -m gwashub_api.worker

EventBridge puts every state change of a GWASHub Batch job on an SQS queue; this loop reads it and
updates the database (jobs.handle_batch_event). A message is deleted only after it was recorded, so
a crash means a retry, and repeated failures land in the dead-letter queue. Every few minutes it also
asks Batch directly about unfinished runs, in case an event never arrived.
"""
import json
import logging
import signal
import time

from sqlalchemy import select

from gwashub_api import jobs
from gwashub_api.config import settings
from gwashub_api.db import SessionLocal
from gwashub_api.models import MetaAnalysis, QCRun

log = logging.getLogger("gwashub.worker")
RECONCILE_SECONDS = 300
_stop = False


def _sqs():
    import boto3
    return boto3.client("sqs", region_name=settings().aws_region)


def handle_message(body: str) -> str:
    db = SessionLocal()
    try:
        return jobs.handle_batch_event(db, json.loads(body))
    finally:
        db.close()


def reconcile():
    """Ask Batch about runs that are still open and record any that finished."""
    db = SessionLocal()
    try:
        open_ids = [r.batch_job_id for r in db.scalars(select(QCRun).where(QCRun.status.in_(("queued", "running")), QCRun.batch_job_id != ""))]
        open_ids += [m.batch_job_id for m in db.scalars(select(MetaAnalysis).where(MetaAnalysis.status.in_(("queued", "running")),
                                                                                    MetaAnalysis.batch_job_id != ""))]
        for i in range(0, len(open_ids), 100):
            for job in jobs._batch().describe_jobs(jobs=open_ids[i:i + 100])["jobs"]:
                if job["status"] in ("SUCCEEDED", "FAILED"):
                    detail = {"jobId": job["jobId"], "status": job["status"], "statusReason": job.get("statusReason"),
                              "container": job.get("container") or {}}
                    log.info("reconcile: %s", jobs.handle_batch_event(db, {"detail": detail}))
    finally:
        db.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings()
    if s.jobs != "batch" or not s.events_queue_url:
        raise SystemExit("The worker needs GWASHUB_JOBS=batch and GWASHUB_EVENTS_QUEUE_URL")

    def stop(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    from gwashub_api.db import create_all
    create_all()  # migrations (the API runs them too; an advisory lock serialises the two)
    sqs, last_reconcile = _sqs(), 0.0
    log.info("Worker started, reading %s", s.events_queue_url)
    while not _stop:
        if time.time() - last_reconcile > RECONCILE_SECONDS:
            try:
                reconcile()
            except Exception:
                log.exception("reconcile failed")
            last_reconcile = time.time()
        try:
            resp = sqs.receive_message(QueueUrl=s.events_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20)
        except Exception:
            log.exception("Could not read the events queue; retrying in 10 s")
            time.sleep(10)
            continue
        for msg in resp.get("Messages", []):
            try:
                log.info(handle_message(msg["Body"]))
                sqs.delete_message(QueueUrl=s.events_queue_url, ReceiptHandle=msg["ReceiptHandle"])
            except Exception:
                log.exception("Could not record message %s; it will be retried", msg.get("MessageId"))
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
