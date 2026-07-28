import contextvars
import logging
import os

trace_id_var = contextvars.ContextVar("trace_id", default="-")


class TraceIdFilter(logging.Filter):
    def filter(self, record):
        record.trace_id = trace_id_var.get("-")
        return True


def get_trace_id() -> str:
    return trace_id_var.get("-")


class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[34m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        levelname = record.levelname
        formatted = super().format(record)
        if levelname in self.COLORS:
            color = self.COLORS[levelname]
            return f"{color}{formatted}{self.RESET}"
        return formatted


LOG_FORMAT = (
    "[%(asctime)s] | [%(trace_id)s] | %(levelname)-5s | "
    "%(filename)s: %(lineno)d |  - %(message)s"
)
LOG_FILE = os.getenv("LOG_FILE", "./app.log")


def get_logger(
    name: str | None = None,
    level: int = logging.DEBUG,
    log_to_file: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        trace_filter = TraceIdFilter()
        stream_handler = logging.StreamHandler()
        stream_handler.stream = open(
            stream_handler.stream.fileno(),
            mode="w",
            encoding="utf-8",
            errors="replace",
            closefd=False,
            buffering=1,
        )
        stream_handler.setFormatter(ColorFormatter(fmt=LOG_FORMAT))
        stream_handler.addFilter(trace_filter)
        logger.addHandler(stream_handler)
        if log_to_file:
            file_handler = logging.FileHandler(LOG_FILE)
            file_handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT))
            file_handler.addFilter(trace_filter)
            logger.addHandler(file_handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


logger = get_logger(__name__)

if __name__ == "__main__":
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")
