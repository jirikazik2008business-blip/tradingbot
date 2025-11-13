from metrics import month_stats
from logger import log_debug
from config import OUTPUT_IMAGE
from image_generator import generate_stats_image

def generate_and_return_image():
    try:
        img = generate_stats_image(OUTPUT_IMAGE)
        return img
    except Exception as e:
        log_debug(f"generate_and_return_image failed: {e}")
        return None
