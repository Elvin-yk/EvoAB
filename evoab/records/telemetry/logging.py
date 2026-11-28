"""evoab logging; level is configured at the CLI boundary."""

import logging


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("evoab")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger("evoab").handlers:
        configure_logging()
    return logging.getLogger(f"evoab.{name}")
