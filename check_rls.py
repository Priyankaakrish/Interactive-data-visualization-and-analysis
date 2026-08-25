from sqlalchemy import text
from src import db
from src.config import load_config

cfg = load_config(None)
with db.connect(cfg) as engine:
    roles = db.fetch(engine, "SELECT rolname FROM pg_roles WHERE rolname LIKE 'bi%' ORDER BY 1")
    print("\nroles created:")
    print(roles.to_string(index=False))

    total = db.fetch(engine, "SELECT COUNT(*) AS n FROM core.fact_sales")["n"].iloc[0]
    print("\nrows visible to each role:")
    print(f"  {'superuser':<16}{total:>12,}")
    scoped = []
    for r in roles["rolname"]:
        try:
            with engine.connect() as c:
                c.execute(text(f"SET ROLE {r}"))
                n = c.execute(text("SELECT COUNT(*) FROM core.fact_sales")).scalar()
            print(f"  {r:<16}{n:>12,}")
            scoped.append((r, n))
        except Exception as e:
            print(f"  {r:<16}{type(e).__name__}")

    narrow = [r for r, n in scoped if n < total]
    if not narrow:
        print("\nno role sees fewer rows than superuser - policies not biting")
    else:
        target = narrow[0]
        emails = db.fetch(engine, "SELECT user_email, access_scope FROM security.user_access ORDER BY access_scope")
        print("\nuser_access table:")
        print(emails.to_string(index=False))
        broadest = emails["user_email"].iloc[-1]
        print(f"\nescalation test: {target} tries to become {broadest}")
        with engine.connect() as c:
            c.execute(text(f"SET ROLE {target}"))
            before = c.execute(text("SELECT COUNT(*) FROM core.fact_sales")).scalar()
            try:
                c.execute(text("SELECT security.set_current_user(:e)"), {"e": broadest})
                note = "function call ALLOWED"
            except Exception as e:
                note = f"blocked -> {type(e).__name__}"
                c.rollback()
                c.execute(text(f"SET ROLE {target}"))
            after = c.execute(text("SELECT COUNT(*) FROM core.fact_sales")).scalar()
        print(f"  {note}")
        print(f"  rows before {before:,} -> after {after:,}")
        print("  PASS - scope unchanged" if before == after else "  FAIL - scope widened")
