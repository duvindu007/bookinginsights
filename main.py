"""Entry point — exposes the ASGI app for `uvicorn main:app` / `python main.py`."""
from dotenv import load_dotenv

# Must load before app.api is imported — database.py/security.py read
# os.environ at import time.
load_dotenv()

from app.api import app  # noqa: E402, F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)
