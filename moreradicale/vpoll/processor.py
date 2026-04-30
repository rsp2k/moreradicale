"""
VPOLL iTIP Processor.

Handles iTIP methods for VPOLL consensus scheduling:
- REQUEST: Distribute poll or update
- REPLY: Submit votes
- STATUS: Broadcast current state
- CANCEL: Terminate poll
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from moreradicale.log import logger
from moreradicale.vpoll.component import (
    VPoll,
    VPollStatus,
    Participant,
    ParticipantType,
    parse_vpoll,
)


class VPollProcessor:
    """
    Processes VPOLL iTIP messages.

    Handles the workflow for creating, voting on, and completing polls.
    """

    def __init__(self, storage, configuration):
        """
        Initialize the VPOLL processor.

        Args:
            storage: Radicale storage instance
            configuration: Radicale configuration
        """
        self._storage = storage
        self._configuration = configuration

    def process_request(self, ical_data: str, user: str,
                        calendar_path: str) -> Tuple[bool, str]:
        """
        Process a VPOLL REQUEST method.

        REQUEST is used to:
        1. Create a new poll and invite voters
        2. Update an existing poll (add/remove items, change deadlines)
        3. Confirm a winner and complete the poll

        Args:
            ical_data: iCalendar data with METHOD:REQUEST
            user: Username processing the request
            calendar_path: Target calendar path

        Returns:
            Tuple of (success, message)
        """
        vpoll = parse_vpoll(ical_data)
        if not vpoll:
            return False, "Failed to parse VPOLL data"

        logger.info("Processing VPOLL REQUEST for poll %s from %s",
                    vpoll.uid, user)

        # Check if this is a new poll or update
        existing = self._find_existing_vpoll(vpoll.uid, calendar_path)

        if existing:
            # Update existing poll
            return self._update_poll(vpoll, existing, user, calendar_path)
        else:
            # Create new poll
            return self._create_poll(vpoll, user, calendar_path)

    def process_reply(self, ical_data: str, user: str,
                      calendar_path: str) -> Tuple[bool, str]:
        """
        Process a VPOLL REPLY method.

        REPLY is used by voters to submit their votes.
        In BASIC mode, a new reply completely replaces the voter's
        previous votes.

        Args:
            ical_data: iCalendar data with METHOD:REPLY
            user: Username processing the reply
            calendar_path: Calendar path where poll is stored

        Returns:
            Tuple of (success, message)
        """
        vpoll = parse_vpoll(ical_data)
        if not vpoll:
            return False, "Failed to parse VPOLL REPLY"

        logger.info("Processing VPOLL REPLY for poll %s from %s",
                    vpoll.uid, user)

        # Find the existing poll
        existing = self._find_existing_vpoll(vpoll.uid, calendar_path)
        if not existing:
            return False, f"Poll {vpoll.uid} not found"

        # Check poll is still accepting votes
        if existing.status not in (VPollStatus.IN_PROCESS,):
            return False, f"Poll is not accepting votes (status: {existing.status.value})"

        # Extract voter from reply
        if not vpoll.participants:
            return False, "REPLY must contain a PARTICIPANT with votes"

        voter = vpoll.participants[0]

        # Verify voter is allowed to vote
        existing_voter = existing.get_participant(voter.email or "")
        if not existing_voter:
            # Add voter if auto-add is enabled
            if self._configuration.get("vpoll", "auto_add_voters"):
                voter.participant_type = ParticipantType.VOTER
                existing.participants.append(voter)
                existing_voter = voter
            else:
                return False, f"Voter {voter.email} not found in poll participants"

        # Replace voter's votes (BASIC mode behavior)
        existing_voter.votes = voter.votes

        # Update the poll
        existing.dtstamp = datetime.now(timezone.utc)

        success = self._save_vpoll(existing, calendar_path)
        if success:
            logger.info("Recorded %d votes from %s for poll %s",
                        len(voter.votes), voter.email, vpoll.uid)
            return True, "Votes recorded successfully"
        else:
            return False, "Failed to save votes"

    def process_cancel(self, ical_data: str, user: str,
                       calendar_path: str) -> Tuple[bool, str]:
        """
        Process a VPOLL CANCEL method.

        CANCEL terminates the poll. Only the poll owner can cancel.

        Args:
            ical_data: iCalendar data with METHOD:CANCEL
            user: Username processing the cancel
            calendar_path: Calendar path where poll is stored

        Returns:
            Tuple of (success, message)
        """
        vpoll = parse_vpoll(ical_data)
        if not vpoll:
            return False, "Failed to parse VPOLL CANCEL"

        logger.info("Processing VPOLL CANCEL for poll %s from %s",
                    vpoll.uid, user)

        # Find the existing poll
        existing = self._find_existing_vpoll(vpoll.uid, calendar_path)
        if not existing:
            return False, f"Poll {vpoll.uid} not found"

        # Verify user is the owner
        owner = existing.owner
        if owner and owner.email:
            owner_email = owner.email.lower()
            user_email = f"{user}@{self._get_internal_domain()}"
            if owner_email != user_email.lower():
                return False, "Only the poll owner can cancel"

        # Update status to cancelled
        existing.status = VPollStatus.CANCELLED
        existing.dtstamp = datetime.now(timezone.utc)
        existing.sequence += 1

        success = self._save_vpoll(existing, calendar_path)
        if success:
            logger.info("Poll %s cancelled by %s", vpoll.uid, user)
            return True, "Poll cancelled"
        else:
            return False, "Failed to cancel poll"

    def confirm_winner(self, poll_uid: str, calendar_path: str,
                       winner_id: Optional[int] = None) -> Tuple[bool, str]:
        """
        Confirm the winning choice for a poll.

        If winner_id is not specified, automatically determine winner
        based on voting results.

        Args:
            poll_uid: UID of the poll
            calendar_path: Calendar path where poll is stored
            winner_id: Optional specific winner ID to confirm

        Returns:
            Tuple of (success, message)
        """
        existing = self._find_existing_vpoll(poll_uid, calendar_path)
        if not existing:
            return False, f"Poll {poll_uid} not found"

        if existing.status not in (VPollStatus.IN_PROCESS, VPollStatus.COMPLETED):
            return False, f"Poll cannot be confirmed (status: {existing.status.value})"

        # Determine winner
        if winner_id is None:
            winner_id = existing.determine_winner()

        if winner_id is None:
            return False, "No votes recorded, cannot determine winner"

        # Verify winner exists
        winner_item = existing.get_item(winner_id)
        if not winner_item:
            return False, f"Invalid winner ID: {winner_id}"

        # Update poll
        existing.poll_winner = winner_id
        existing.status = VPollStatus.CONFIRMED
        existing.dtstamp = datetime.now(timezone.utc)
        existing.sequence += 1

        success = self._save_vpoll(existing, calendar_path)
        if success:
            logger.info("Poll %s confirmed with winner %d (%s)",
                        poll_uid, winner_id, winner_item.summary)
            return True, f"Winner confirmed: {winner_item.summary}"
        else:
            return False, "Failed to confirm winner"

    def get_poll_status(self, poll_uid: str,
                        calendar_path: str) -> Optional[VPoll]:
        """
        Get the current status of a poll.

        Args:
            poll_uid: UID of the poll
            calendar_path: Calendar path where poll is stored

        Returns:
            VPoll object or None if not found
        """
        return self._find_existing_vpoll(poll_uid, calendar_path)

    def generate_status_message(self, poll: VPoll) -> str:
        """
        Generate an iTIP STATUS message for a poll.

        The STATUS method broadcasts current poll state to all voters.

        Args:
            poll: VPoll object

        Returns:
            iCalendar formatted STATUS message
        """
        return poll.to_ical(method="STATUS")

    def _create_poll(self, vpoll: VPoll, user: str,
                     calendar_path: str) -> Tuple[bool, str]:
        """Create a new poll."""
        # Ensure poll has an owner
        if not vpoll.owner:
            # Create owner participant from user
            owner = Participant(
                uid=f"owner-{vpoll.uid}",
                calendar_address=f"mailto:{user}@{self._get_internal_domain()}",
                participant_type=ParticipantType.OWNER,
            )
            vpoll.participants.insert(0, owner)

        # Set initial status
        if vpoll.status not in (VPollStatus.IN_PROCESS,):
            vpoll.status = VPollStatus.IN_PROCESS

        # Set organizer
        if not vpoll.organizer:
            vpoll.organizer = f"mailto:{user}@{self._get_internal_domain()}"

        success = self._save_vpoll(vpoll, calendar_path)
        if success:
            logger.info("Created new poll %s with %d items and %d participants",
                        vpoll.uid, len(vpoll.items), len(vpoll.participants))
            return True, "Poll created successfully"
        else:
            return False, "Failed to create poll"

    def _update_poll(self, vpoll: VPoll, existing: VPoll, user: str,
                     calendar_path: str) -> Tuple[bool, str]:
        """Update an existing poll."""
        # Verify user is the owner
        owner = existing.owner
        if owner and owner.email:
            owner_email = owner.email.lower()
            user_email = f"{user}@{self._get_internal_domain()}"
            if owner_email != user_email.lower():
                return False, "Only the poll owner can update"

        # Update properties
        if vpoll.summary:
            existing.summary = vpoll.summary
        if vpoll.description:
            existing.description = vpoll.description
        if vpoll.dtstart:
            existing.dtstart = vpoll.dtstart
        if vpoll.dtend:
            existing.dtend = vpoll.dtend
        if vpoll.poll_properties:
            existing.poll_properties = vpoll.poll_properties

        # Update status if confirming
        if vpoll.status == VPollStatus.CONFIRMED:
            existing.status = VPollStatus.CONFIRMED
            if vpoll.poll_winner is not None:
                existing.poll_winner = vpoll.poll_winner

        # Update items if provided
        if vpoll.items:
            existing.items = vpoll.items

        # Add new participants (don't remove existing)
        for new_participant in vpoll.participants:
            if not existing.get_participant(new_participant.email or ""):
                existing.participants.append(new_participant)

        existing.dtstamp = datetime.now(timezone.utc)
        existing.sequence += 1

        success = self._save_vpoll(existing, calendar_path)
        if success:
            logger.info("Updated poll %s", vpoll.uid)
            return True, "Poll updated successfully"
        else:
            return False, "Failed to update poll"

    def _find_existing_vpoll(self, uid: str,
                             calendar_path: str) -> Optional[VPoll]:
        """Find an existing VPOLL by UID in the calendar."""
        try:
            # Discover items in the calendar
            items = list(self._storage.discover(calendar_path, depth="1"))

            for item in items:
                if hasattr(item, 'serialize'):
                    content = item.serialize()
                    if "BEGIN:VPOLL" in content:
                        vpoll = parse_vpoll(content)
                        if vpoll and vpoll.uid == uid:
                            return vpoll

        except Exception as e:
            logger.warning("Error searching for VPOLL %s: %s", uid, e)

        return None

    def _save_vpoll(self, vpoll: VPoll, calendar_path: str) -> bool:
        """Save a VPOLL to storage."""
        try:
            ical_data = vpoll.to_ical()

            # Find the calendar collection
            items = list(self._storage.discover(calendar_path, depth="0"))
            if not items:
                logger.warning("Calendar not found: %s", calendar_path)
                return False

            calendar = items[0]

            # Create or update the item
            href = f"{vpoll.uid}.ics"

            # Try to import as a single item
            from moreradicale import item as radicale_item
            prepared_item = radicale_item.Item(
                collection_path=calendar_path,
                text=ical_data,
            )
            prepared_item.prepare()

            calendar.upload(href, prepared_item)
            return True

        except Exception as e:
            logger.error("Error saving VPOLL %s: %s", vpoll.uid, e, exc_info=True)
            return False

    def _get_internal_domain(self) -> str:
        """Get the internal domain from configuration."""
        return self._configuration.get("scheduling", "internal_domain")
