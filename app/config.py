import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://campus:campus@localhost:5432/campusmarket")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Bishkek")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 5
PER_PAGE = 8
