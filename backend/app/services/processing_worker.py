"""Poll the durable Mongo queue; the orchestrator owns classification and alerts."""
import logging
from threading import Event

from app.db.repositories.record_repo import RecordRepo

log = logging.getLogger(__name__)


class ProcessingWorker:
    def __init__(self, repo: RecordRepo | None = None, orchestrator=None):
        self.repo = repo or RecordRepo()
        self.orchestrator = orchestrator

    def run_once(self) -> bool:
        record = self.repo.claim_next()
        if record is None:
            return False
        try:
            from app.services.orchestrator import get_orchestrator
            orchestrator = self.orchestrator or get_orchestrator()
            alert_id = orchestrator.handle_record(record)
        except Exception as exc:
            # Vendor errors can contain URLs or credentials. Keep them out of the API.
            self.repo.mark_error(record["record_id"], f"Processing failed ({type(exc).__name__})",
                                 attempt=record["attempts"], lease_token=record["lease_token"])
        else:
            self.repo.mark_processed(record["record_id"], alert_id, attempt=record["attempts"],
                                     lease_token=record["lease_token"])
        return True

    def run(self, stop: Event, poll_seconds: float = 2) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                log.warning("Processing queue unavailable (%s)", type(exc).__name__)
            stop.wait(poll_seconds)


if __name__ == "__main__":
    from app.config import get_settings
    from app.db import mongo

    logging.basicConfig(level=logging.INFO)
    mongo.ensure_indexes()
    stop = Event()
    try:
        ProcessingWorker().run(stop, get_settings().PROCESSING_WORKER_POLL_SECONDS)
    except KeyboardInterrupt:
        stop.set()
