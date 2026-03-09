"""Shared types for corp-rfp-agent."""

from dataclasses import dataclass, field
from enum import Enum


class Family(str, Enum):
    """Product family codes."""
    PLANNING = "planning"
    WMS = "wms"
    LOGISTICS = "logistics"
    SCPO = "scpo"
    CATMAN = "catman"
    WORKFORCE = "workforce"
    COMMERCE = "commerce"
    FLEXIS = "flexis"
    NETWORK = "network"
    DODDLE = "doddle"
    AIML = "aiml"


class Category(str, Enum):
    """KB entry categories -- 4 RFP response teams."""
    TECHNICAL = "technical"              # Platform architects: architecture, APIs, security, SLA
    FUNCTIONAL = "functional"            # Product consultants: business capabilities, workflows, UI
    CUSTOMER_EXECUTIVE = "customer_executive"  # Sales: company overview, references, licensing
    CONSULTING = "consulting"            # Implementation: methodology, training, migration


class Confidence(str, Enum):
    """KB entry confidence levels."""
    VERIFIED = "verified"
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    OUTDATED = "outdated"


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    text: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0


@dataclass
class KBMatch:
    """A single KB retrieval result."""
    entry_id: str
    question: str
    answer: str
    similarity: float
    family_code: str = ""
    category: str = ""
    metadata: dict = field(default_factory=dict)
