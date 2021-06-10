import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)

from models.models import Base, auto_str


@enum.unique
class ModerationAction(enum.Enum):
    """Which moderation action a particular row represents.

    Values are specified to ensure they always match up with the database.
    """

    TEMPMUTE = 0
    MUTE = 1
    UNMUTE = 2
    WARN = 3
    REMOVE_WARN = 4
    AUTOWARN = 5
    REMOVE_AUTOWARN = 6
    KICK = 7
    TEMPBAN = 8
    BAN = 9
    UNBAN = 10


@auto_str
class ModerationHistory(Base):
    __tablename__ = "moderation_history"

    id = Column(Integer, primary_key=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=func.current_timestamp())
    action = Column(Enum(ModerationAction), nullable=False)
    until = Column(DateTime, nullable=True)
    complete = Column(Boolean, nullable=True)
    reason = Column(String, nullable=True)
    moderator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    linked_item = Column(Integer, ForeignKey("moderation_history.id"), nullable=True)


@auto_str
class ModerationTemporaryActions(Base):
    __tablename__ = "moderation_temporary_actions"

    id = Column(Integer, ForeignKey("moderation_history.id"), primary_key=True, nullable=False)
    until = Column(DateTime, nullable=False)
    complete = Column(Boolean, nullable=False)


@auto_str
class ModerationLinkedItems(Base):
    __tablename__ = "moderation_linked_items"

    id = Column(Integer, ForeignKey("moderation_history.id"), primary_key=True, nullable=False)
    linked_item = Column(Integer, ForeignKey("moderation_history.id"), nullable=False)
