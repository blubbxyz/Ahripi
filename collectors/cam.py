import os
import logging
from datetime import datetime
from typing import Optional, Dict

from picamera2 import Picamera2

log = logging.getLogger(__name__)

PHOTO_DIR = os.getenv("PHOTO_DIR", "/home/blubb/ahripi-dev/data/pi-cam")
os.makedirs(PHOTO_DIR, exist_ok=True)

_picam2: Optional[Picamera2] = None


def init_camera() -> Picamera2:
    """
    Initialize Picamera2 once (global singleton).
    Important: Do NOT create a new Picamera2() per request.
    """
    global _picam2

    if _picam2 is not None:
        return _picam2

    cam = Picamera2()
    cam.configure(cam.create_still_configuration())
    cam.start()

    _picam2 = cam
    log.info("Picamera2 initialized and started")
    return _picam2


def capture_photo() -> Dict[str, str]:
    """
    Captures a photo and returns metadata you can return from Flask.

    Returns:
      {
        "filename": "...jpg",
        "path": "/abs/path/to/file.jpg",
        "url": "/photos/...jpg",
        "timestamp": "..."
      }
    """
    cam = init_camera()

    filename = datetime.now().strftime("photo_%Y-%m-%d_%H-%M-%S.jpg")
    path = os.path.join(PHOTO_DIR, filename)

    cam.capture_file(path)

    return {
        "filename": filename,
        "path": path,
        "url": f"/photos/{filename}",
        "timestamp": datetime.now().isoformat(),
    }


def close_camera() -> None:
    """
    Optional cleanup. Usually not needed unless you want graceful shutdown.
    """
    global _picam2
    if _picam2 is not None:
        try:
            _picam2.close()
        finally:
            _picam2 = None
            log.info("Picamera2 closed")
