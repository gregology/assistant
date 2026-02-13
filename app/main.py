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
