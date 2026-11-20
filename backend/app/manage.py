"""Admin commands. Run inside the backend container / Railway shell / Render shell:

    python -m app.manage reseed               # drop demo tenants + all sandboxes, re-seed fresh data
    python -m app.manage cleanup-sandboxes    # delete expired demo sandboxes now
"""

import sys

from sqlalchemy import select

from app.db.models import Sandbox, Tenant
from app.db.session import SessionLocal, init_db
from app.services import sandbox
from app.services.alerts import refresh_alerts
from app.services.analytics import AnalyticsEngine
from app.services.seed import PROFILES, seed_all


def reseed() -> None:
    init_db()
    with SessionLocal() as s:
        sandbox.drop_tenants(s, list(s.scalars(select(Sandbox.tenant_id))))
        ids = [p.tenant_id for p in PROFILES if s.get(Tenant, p.tenant_id) is not None]
        sandbox.drop_tenants(s, ids)
        s.commit()
        for tid in seed_all(s):
            engine = AnalyticsEngine.for_tenant(s, tid)
            try:
                refresh_alerts(s, tid, engine)
            finally:
                engine.close()
            print(f"seeded {tid}")


def cleanup() -> None:
    init_db()
    with SessionLocal() as s:
        print(f"removed {len(sandbox.cleanup_expired(s))} expired sandboxes")


if __name__ == "__main__":
    commands = {"reseed": reseed, "cleanup-sandboxes": cleanup}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(__doc__)
        sys.exit(2)
    commands[sys.argv[1]]()
