from fastapi import FastAPI

from app import queue

app = FastAPI()
queue.init()


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/foo")
async def create_task(body: dict, priority: int = 5):
    task_id = queue.enqueue(body, priority=priority)
    return {"task_id": task_id, "status": "pending"}


@app.post("/check_email")
async def check_email(limit: int = 10):
    task_id = queue.enqueue({"type": "check_email", "limit": limit})
    return {"task_id": task_id, "status": "pending"}
