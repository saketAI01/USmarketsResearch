import logging
import os
import sys

from pathlib import Path

log_file = Path(__file__).resolve().parent.parent.parent / "stock_evaluate.log"

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

fh = logging.FileHandler(str(log_file), mode='a', encoding='utf-8')
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
root_logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.WARNING)
ch.setFormatter(formatter)
root_logger.addHandler(ch)


def get_logger(name):
    return logging.getLogger(name)


logger = get_logger("System")
logger.info("--- Stock Evaluate Session Started ---")
