"""Entry point — exposes the ASGI app for `uvicorn main:app` / `python main.py`."""
from dotenv import load_dotenv


load_dotenv()

from app.api import app  

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)
