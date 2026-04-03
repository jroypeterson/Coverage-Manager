import logging
import sys


def configure_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name):
    return logging.getLogger(name)


def log_exception(logger, message, exc):
    logger.warning("%s: %s", message, exc)
