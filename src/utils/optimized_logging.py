
import logging
import logging.handlers
import gzip
import shutil
import os
from pathlib import Path


def setup_optimized_logging(logger_name, log_level=logging.INFO):
    """Configuration logging optimisée avec compression et rotation"""

    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)

    # Éviter duplication
    if logger.handlers:
        logger.handlers.clear()

    # Formatter optimisé
    formatter = logging.Formatter(
        '%(asctime)s|%(levelname)s|%(funcName)s|%(message)s',
        datefmt='%H:%M:%S'
    )

    # Handler fichier avec compression
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    def rotator(source, dest):
        """Compresser lors de la rotation"""
        with open(source, "rb") as f_in:
            with gzip.open(dest + ".gz", "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(source)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{logger_name}.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.rotator = rotator
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Handler console minimal
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Moins verbeux
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
