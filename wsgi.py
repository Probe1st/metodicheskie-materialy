from dotenv import load_dotenv

load_dotenv()

from app.web import create_app

app = create_app()
