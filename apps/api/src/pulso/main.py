from fastapi import FastAPI

app = FastAPI(title="Pulso Promos API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
