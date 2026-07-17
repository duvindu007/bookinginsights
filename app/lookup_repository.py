
import logging
from typing import Dict, Set, Type

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LookupRepository:
    def __init__(self, db: Session, model: Type):
        self._db = db
        self._model = model

    def get_or_create_many(self, names: Set[str]) -> Dict[str, int]:

        if not names:
            return {}

        existing = (
            self._db.query(self._model.name, self._model.id)
            .filter(self._model.name.in_(names))
            .all()
        )
        name_to_id: Dict[str, int] = {name: id_ for name, id_ in existing}

        missing = names - name_to_id.keys()
        if missing:
            new_rows = [self._model(name=name) for name in missing]
            self._db.add_all(new_rows)
            self._db.flush()  # assigns ids without committing yet
            for row in new_rows:
                name_to_id[row.name] = row.id
            logger.info(
                "Created %d new %s row(s): %s",
                len(missing), self._model.__tablename__, sorted(missing),
            )

        return name_to_id
