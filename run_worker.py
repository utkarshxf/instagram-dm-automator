import asyncio
from arq.worker import run_worker
from backend.app.workers.tasks import WorkerSettings

if __name__ == "__main__":
    try:
        # Create a loop and set it
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # arq.run_worker internally calls create_worker and then worker.run()
        # The issue is in create_worker calling Worker() which calls get_event_loop()
        # By setting the event loop here, it should fix the issue.
        run_worker(WorkerSettings)
    except Exception as e:
        print(f"Error starting worker: {e}")
        import traceback
        traceback.print_exc()
