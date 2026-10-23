"""Transcript-based OSOP recorder.

Parses Claude Code JSONL session transcripts into phases of tool calls with
their real results (not LLM self-report), and renders a receipt from them.
"""

from osop.recorder.transcript import (
    PhaseNode,
    ToolCall,
    parse_transcript,
    resolve_transcript_path,
)

__all__ = [
    "PhaseNode",
    "ToolCall",
    "parse_transcript",
    "resolve_transcript_path",
]
