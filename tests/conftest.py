import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(ROOT, "src", "pipeline")

if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)
