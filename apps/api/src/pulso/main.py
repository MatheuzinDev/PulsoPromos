from fastapi import FastAPI

from pulso.catalog.errors import register_exception_handlers
from pulso.catalog.router import router as catalog_router

app = FastAPI(title="Pulso Promos API")
register_exception_handlers(app)
app.include_router(catalog_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
