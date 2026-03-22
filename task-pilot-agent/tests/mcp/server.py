from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
import uvicorn
import time
old__received_request = ServerSession._received_request

async def _received_request(self, *args, **kwargs):
    try:
        return await old__received_request(self, *args, **kwargs)
    except RuntimeError:
        pass

ServerSession._received_request = _received_request
mcp = FastMCP(name="Progress Example")


@mcp.tool()
async def long_running_task(task_name: str, steps: int, ctx: Context[ServerSession, None]) -> str:
    """Execute a task with progress updates."""
    await ctx.info(f"Starting: {task_name}")

    for i in range(steps):
        progress = (i + 1) / steps
        await ctx.report_progress(
            progress=progress,
            total=1.0,
            message=f"Step {i + 1}/{steps}",
        )
        time.sleep(1)
        await ctx.debug(f"Completed step {i + 1}")
        await ctx.error("Error: (This is just a demo)")

    return f"Task '{task_name}' completed"
if __name__ == "__main__":
    # 用官方 SDK 提供的 ASGI 应用，然后自己起 uvicorn 指定 host/port
    app = mcp.streamable_http_app()  # 默认路径 /mcp
    uvicorn.run(app, host="127.0.0.1", port=8000)
