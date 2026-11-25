"""People fixtures a test arranges a Library with (#1426).

The domain commands take a Person's fields as one mapping, and a Work's
initial roles as :class:`roles.InitialRole` rows. These build both from
plain arguments.
"""

from __future__ import annotations

from typing import Optional

from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.request_context import RequestContext
from backend.domain.identity.library_scope import library_command
from backend.domain.people import person_commands, roles


def add_person(database: Database, ctx: RequestContext, first_name: str, last_name: str,
               aliases: str = "", about: str = "", image_url: str = "", link_wikipedia: str = "",
               link_stanford_encyclopedia: str = "", link_iep: str = "", links_other: str = "",
               birth_date: str = "", death_date: str = "", person_id: Optional[str] = None) -> str:
    """A new Person's id, from its fields as arguments."""
    fields = {
        "first_name": first_name, "last_name": last_name, "aliases": aliases, "about": about,
        "image_url": image_url, "link_wikipedia": link_wikipedia,
        "link_stanford_encyclopedia": link_stanford_encyclopedia, "link_iep": link_iep,
        "links_other": links_other, "birth_date": birth_date, "death_date": death_date,
    }
    return person_commands.create(database, ctx, fields, person_id)


def insert_initial_role(database: Database, ctx: RequestContext, work_id: str, person_id: str,
                        role_type: str, order_index: int = 0, credit_name: str = "") -> None:
    """A role a new Work is born with, as its create stores it: no revision moves."""
    wanted = [roles.InitialRole(person_id, role_type, int(order_index), credit_name or "")]
    library_command(database, ctx, Capability.EDIT_LIBRARY,
                    lambda scope: roles.insert_initial_roles(scope, work_id, wanted, skip_invalid=False),
                    isolation=Isolation.SERIALIZABLE)
