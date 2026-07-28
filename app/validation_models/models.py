from pydantic import BaseModel, Field


class StreamEventsRequest(BaseModel):
    session_id: str = Field(..., description="The session ID of the chat")
    prompt_id: str = Field(..., description="The prompt ID of the chat")
    block_ms: int = Field(default=100, description="The block size in milliseconds")
    start_id: str = Field(default="0-0", description="The start ID of the chat")
