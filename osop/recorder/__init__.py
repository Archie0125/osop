"""Transcript-based OSOP recorder.

Parses Claude Code JSONL session transcripts and synthesizes .osoplog
execution records from real tool-call evidence (not LLM self-report).
"""

from osop.recorder.transcript import (
    PhaseNode,
    ToolCall,
    parse_transcript,
    resolve_transcript_path,
    synthesize,
)

__all__ = [
    "PhaseNode",
    "ToolCall",
    "parse_transcript",
    "resolve_transcript_path",
    "synthesize",
]
