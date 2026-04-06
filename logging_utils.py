import functools
import logging
import random
import sys
import time


def retry_on_failure(max_retries=2, base_delay=1.0, logger_name=None):
    """Decorator that retries a function on exception with exponential backoff.

    Args:
        max_retries: Number of retry attempts after the first failure.
        base_delay: Base delay in seconds (doubled each retry, with jitter).
        logger_name: Logger name for retry warnings. Uses function module if None.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _logger = logging.getLogger(logger_name or fn.__module__)
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                        _logger.warning(
                            "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                            fn.__name__, attempt + 1, max_retries + 1, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        _logger.warning(
                            "%s failed after %d attempts: %s",
                            fn.__name__, max_retries + 1, e,
                        )
            raise last_exc
        return wrapper
    return decorator


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
