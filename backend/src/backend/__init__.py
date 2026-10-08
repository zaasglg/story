from backend.main import create_app

app = create_app()


def main() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend:app", host="0.0.0.0", port=port)
