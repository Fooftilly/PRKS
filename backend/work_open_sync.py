"""Work open events: MARK_WORK_OPENED and the canonical `last_opened_at` rule.

Unlike a Work-Tag edit, an open event carries no optimistic concurrency and can
never conflict. `last_opened_at` is a MAX-REGISTER over normalized event times:
the canonical value is the latest instant at which the Work was actually
opened, whichever device reports it and whenever that report arrives.

That is what makes the operation commutative, idempotent and order-independent.
A device that reconnects on Friday carrying Monday's open cannot drag the Work
back to Monday, and must not claim it was opened on Friday either -- which is
why arrival time is not the canonical event time, and why this is a max-register
rather than last-writer-wins.

Nothing here imports the database layer: the handler is handed the `db` it
needs, so this module stays a pure domain rule that the direct
`POST /api/works/:id/opened` endpoint can share.
"""
from datetime import datetime, timedelta, timezone

# A client clock running fast could otherwise pin a Work to the top of Recent
# for as long as it is wrong. Small enough that ordinary clock drift and
# request latency never reach it.
MAX_CLIENT_FUTURE_SKEW_SECONDS = 300


def format_moment(value):
    """The one canonical sortable SQLite text form: UTC, millisecond precision.

    `YYYY-MM-DD HH:MM:SS.mmm` sorts lexicographically in chronological order,
    and keeps sorting correctly against the second-resolution values already in
    the column, because a bare `12:00:00` is a prefix of `12:00:00.250`.
    """
    moment = value.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S") + ".%03d" % (moment.microsecond // 1000)


def effective_opened_at(occurred_at, received_at):
    """The event time the server will actually record.

    An OLD timestamp is legitimate: a Work really can have been opened days ago
    on a device that was offline, and the max-register makes an obsolete event a
    harmless no-op. A timestamp far in the FUTURE is not legitimate, but the
    activity behind it is -- so it is clamped to when the server heard about it
    rather than discarded.
    """
    moment = datetime.fromisoformat(occurred_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    horizon = received_at + timedelta(seconds=MAX_CLIENT_FUTURE_SKEW_SECONDS)
    return format_moment(received_at if moment > horizon else moment)


def validate(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    # An open event has no prior state it could be stale against, so a
    # base_revision would be a claim this family cannot honor.
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


