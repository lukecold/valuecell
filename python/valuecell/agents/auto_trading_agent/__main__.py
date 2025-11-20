"""Main entry point for auto trading agent"""

import asyncio
import logging

from valuecell.core.agent.decorator import create_wrapped_agent

from .agent import AutoTradingAgent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    agent = create_wrapped_agent(AutoTradingAgent)
    try:
        asyncio.run(agent.serve())
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)