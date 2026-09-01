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
img.save('G:/Python/jcspy/Super_ADB/shot_about_open.png')
print('saved')
