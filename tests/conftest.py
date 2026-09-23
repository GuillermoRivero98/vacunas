import os
import sys
import warnings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
warnings.filterwarnings("ignore", category=FutureWarning)
