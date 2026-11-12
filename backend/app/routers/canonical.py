from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules import scd2_history
from app.repositories import canonical as canon_repo

router = APIRouter(tags=["canonical"])


@router.get("/canonical")
def list_canonical():
    """Current effective master rows — one open version per business key."""
    conn = connect()
    try:
        return canon_repo.list_items(conn)
    finally:
        conn.close()


@router.get("/canonical/{business_key}/history")
def get_canonical_history(business_key: str):
    """Full SCD2 version chain of a key, newest version first."""
    conn = connect()
    try:
        if not canon_repo.get_by_key(conn, business_key):
            raise HTTPException(404, "not found")
        return scd2_history.list_versions(conn, business_key)
    finally:
        conn.close()


@router.get("/canonical/{business_key}")
def get_canonical(business_key: str):
    conn = connect()
    try:
        row = canon_repo.get_by_key(conn, business_key)
        if not row:
            raise HTTPException(404, "not found")
        return row
    finally:
        conn.close()
