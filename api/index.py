from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok", "service": "movie-box API"}

@app.get("/health")
def health():
    return {"status": "ok"}
