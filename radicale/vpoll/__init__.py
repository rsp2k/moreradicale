"""
VPOLL: Consensus Scheduling Component for iCalendar.

This module implements draft-ietf-calext-vpoll for consensus scheduling,
allowing users to create polls for meeting times and collect votes.

Key components:
- VPOLL: Container for poll items and participants
- PARTICIPANT: Identifies poll owners and voters
- VOTE: Records individual voting responses

Key properties:
- POLL-ITEM-ID: Unique identifier for voteable items
- POLL-MODE: Voting methodology (BASIC)
- POLL-COMPLETION: Who determines and submits winner
- POLL-WINNER: The winning item after confirmation
- RESPONSE: Voter preference (0-100 scale)
"""

from radicale.vpoll.component import (
    VPollStatus,
    PollMode,
    PollCompletion,
    ParticipantType,
    VPoll,
    Participant,
    Vote,
    parse_vpoll,
    serialize_vpoll,
)
from radicale.vpoll.processor import VPollProcessor

__all__ = [
    "VPollStatus",
    "PollMode",
    "PollCompletion",
    "ParticipantType",
    "VPoll",
    "Participant",
    "Vote",
    "parse_vpoll",
    "serialize_vpoll",
    "VPollProcessor",
]
