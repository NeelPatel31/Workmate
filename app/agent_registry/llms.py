from langchain_openai import AzureChatOpenAI

from ..config import settings
from ..utils import logger

model = AzureChatOpenAI(
    api_key=settings.AZURE_OPENAI_API_KEY,
    model=settings.AZURE_OPENAI_MODEL_NAME,
    api_version=settings.AZURE_OPENAI_API_VERSION,
    azure_endpoint=settings.AZURE_OPENAI_API_ENDPOINT,
    temperature=0.0,
)

# Backwards-compatible alias used by some older imports
llm = model


async def check_llm_health():
    try:
        await model.ainvoke("hi")
        return True
    except Exception as e:
        logger.error(f"LLM health check failed: {e}")
        return False
