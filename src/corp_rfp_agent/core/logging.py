"""Centralized logging setup with Rich fallback."""

import logging


def setup_logging(level: str = "INFO", rich: bool = True) -> None:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        rich: Use Rich handler for pretty output (falls back to standard if unavailable)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    if rich:
        try:
            from rich.logging import RichHandler
            logging.basicConfig(
                level=log_level,
                format="%(message)s",
                datefmt="[%X]",
                handlers=[RichHandler(rich_tracebacks=True)],
            )
            return
        except ImportError:
            pass

    # Fallback: standard logging
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
