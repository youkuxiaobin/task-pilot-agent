import sys
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("APP_CONFIG_FILE", str(project_root / "config" / "config.yaml"))

from config.config import agentSettings  # noqa: E402
from tools.aggre_mcp_market.app import aggre_mcp_market_router, init_mcp_market_registry  # noqa: E402

from brain.app import agent_router  # noqa: E402
from file.file_op import file_router  # noqa: E402
from auth.hardening import validate_auth_production_config  # noqa: E402
from auth.router import auth_router  # noqa: E402
from utils.logger import get_logger  # noqa: E402
from mcp_process import _set_proctitle  # noqa: E402

logger = get_logger(__name__)
uvicorn_workers = int(os.getenv("UVICORN_WORKERS", "5"))


async def _bootstrap():
    logger.info("Starting bootstrap in worker...")
    _set_proctitle("taskpilotagent-api-worker")
    validate_auth_production_config(agentSettings)

    # MCP Market Registry 初始化
    try:
        await init_mcp_market_registry()
        logger.info("MCP market registry initialized")
    except Exception as e:
        logger.error(f"Error initializing MCP market registry: {e}")

    logger.info(
        f"Worker initialized. MCP address: "
        f"{agentSettings.mcp.mcp_local.transport}://"
        f"{agentSettings.mcp.mcp_local.host}:"
        f"{agentSettings.mcp.mcp_local.port}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await _bootstrap()
    
    yield
    
    logger.info("Application shutting down...")


# 创建 FastAPI 应用
app = FastAPI(lifespan=lifespan)
app.include_router(aggre_mcp_market_router, prefix="/aggre_mcp_market")
app.include_router(auth_router, prefix="/auth")
app.include_router(agent_router, prefix="/agent")
app.include_router(file_router, prefix="/file/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok", "workers": uvicorn_workers}
