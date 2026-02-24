import logging
import sys
from pythonjsonlogger import jsonlogger
from .config import settings

def setup_logging():
    log_handler = logging.StreamHandler(sys.stdout)
    
    if settings.JSON_LOGS:
        formatter = jsonlogger.JsonFormatter(
            fmt='%(asctime)s %(levelname)s %(name)s %(message)s'
        )
    else:
        formatter = logging.Formatter(
            fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        )
    
    log_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)
    root_logger.setLevel(settings.LOG_LEVEL)
    
    # Mute noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("motor").setLevel(logging.WARNING)
    
    return root_logger

logger = logging.getLogger("ig-automator")
