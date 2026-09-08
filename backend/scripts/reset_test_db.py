import os

os.environ["LLM_MODE"] = "deterministic"

from sqlalchemy import create_engine, text

from backend.infrastructure.config import clear_settings_cache, get_settings
from backend.models import Base

clear_settings_cache()
s = get_settings()
print("db", s.database_url.split("@")[-1])
print("test", (s.test_database_url or "").split("@")[-1])
print("same", s.test_database_url == s.database_url)
url = s.test_database_url or s.database_url
eng = create_engine(url)
Base.metadata.drop_all(eng)
Base.metadata.create_all(eng)
with eng.connect() as c:
    n = c.execute(
        text(
            "select count(*) from information_schema.tables "
            "where table_schema='public'"
        )
    ).scalar()
    print("tables", n)
