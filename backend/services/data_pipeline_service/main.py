from __future__ import annotations

import uvicorn

from data_pipeline.constants import PIPELINE_SCHEMA
from data_pipeline.schedulers import start_pipeline_scheduler, stop_pipeline_scheduler
from services.bootstrap import bootstrap_data_pipeline
from services.data_pipeline_service.routes import routers
from shared.app_factory import create_service_app
from shared.background_queue import start_background_queue_worker, stop_background_queue_worker
from shared.config import service_port

app = create_service_app(
    service_name="data-pipeline",
    title="Pulse Engine Data Pipeline Service",
    routers=routers,
    db_schema=PIPELINE_SCHEMA,
    startup_tasks=(bootstrap_data_pipeline,),
)


@app.on_event("startup")
async def start_scheduler() -> None:
    app.state.pipeline_scheduler_handle = await start_pipeline_scheduler(app.state.db)
    # Drain pipeline jobs from the shared Redis background queue (process-raw-*)
    # so queued raw_* records progress even when enqueued by other services.
    app.state.background_worker_handle = await start_background_queue_worker(
        app.state.db, service_label="data-pipeline"
    )


@app.on_event("shutdown")
async def stop_scheduler() -> None:
    await stop_pipeline_scheduler(getattr(app.state, "pipeline_scheduler_handle", None))
    await stop_background_queue_worker(getattr(app.state, "background_worker_handle", None))


if __name__ == "__main__":
    uvicorn.run(
        "services.data_pipeline_service.main:app",
        host="0.0.0.0",
        port=service_port(8012),
    )
