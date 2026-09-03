import sys
import time
from pathlib import Path

try:
    from PIL import ImageGrab
except ImportError:
    print('PIL not available')
    sys.exit(1)

time.sleep(4)
img = ImageGrab.grab()
OUT = Path(__file__).resolve().parent / 'shot_about_open.png'
img.save(str(OUT))
print('saved')
