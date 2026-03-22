# -*- coding: utf-8 -*-
import faulthandler
import uvicorn
from utils.logger import (
    configure_logging,
    get_logger,
    build_logging_config,
)
from app_main import agentSettings, uvicorn_workers
from mcp_process import start_mcp_subprocess, cleanup_mcp_process, _set_proctitle

logger = get_logger(__name__)
faulthandler.enable()


if __name__ == "__main__":
    mcp_process = None
    try:
        configure_logging(force=True)
        _set_proctitle("taskpilotagent-master")
        mcp_process = start_mcp_subprocess()

        logger.info(
            f"Starting FastAPI server on "
            f"{agentSettings.server.host}:{agentSettings.server.port} "
            f"with {uvicorn_workers} workers"
        )

        uvicorn.run(
            "app_main:app",
            host=agentSettings.server.host,
            port=agentSettings.server.port,
            reload=False,
            workers=uvicorn_workers,
            log_config=build_logging_config(),
            access_log=True,
        )

    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
    finally:
        cleanup_mcp_process(mcp_process)
        logger.info("Application shutdown complete")
