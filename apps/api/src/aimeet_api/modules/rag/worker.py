import logging
import signal
import threading

from aimeet_api.core.config import Settings
from aimeet_api.db.session import create_engine_and_session
from aimeet_api.modules.rag.indexing import IndexWorker


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    settings = Settings()
    engine, sessions = create_engine_and_session(settings)
    worker = IndexWorker(sessions, settings)
    try:
        while not stop.is_set():
            try:
                worked = worker.run_once()
            except Exception:
                logging.error("RAG worker database unavailable; retrying")
                worked = False
            if not worked:
                stop.wait(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
